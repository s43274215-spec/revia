import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class SecretStorageError(RuntimeError):
    pass


class SecretTransportError(ValueError):
    pass


class CredentialCipher:
    algorithm = "fernet-v1"

    def __init__(self, encryption_key: str) -> None:
        try:
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise SecretStorageError("服务端凭证加密密钥配置无效") from exc

    def encrypt(self, api_key: str) -> bytes:
        normalized = api_key.strip()
        if not normalized:
            raise SecretStorageError("API Key 不能为空")
        return self._fernet.encrypt(normalized.encode("utf-8"))

    def decrypt(self, encrypted_secret: bytes) -> str:
        try:
            return self._fernet.decrypt(encrypted_secret).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise SecretStorageError("已保存的 API Key 无法解密") from exc


class TransportKeyPair:
    def __init__(self) -> None:
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def public_key_pem(self) -> str:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    def decrypt(self, encrypted_api_key: str) -> str:
        try:
            encrypted = base64.b64decode(encrypted_api_key, validate=True)
            plaintext = self._private_key.decrypt(
                encrypted,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            ).decode("utf-8").strip()
        except Exception as exc:
            raise SecretTransportError("API Key 加密数据无效，请重新输入") from exc
        if not plaintext:
            raise SecretTransportError("API Key 不能为空")
        return plaintext


@lru_cache
def get_transport_key_pair() -> TransportKeyPair:
    return TransportKeyPair()
