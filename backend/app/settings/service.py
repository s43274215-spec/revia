import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.clients.base import AIClient, AIRequestError
from app.ai.clients.deepseek import DeepSeekClient
from app.core.config import Settings
from app.models.workspace import DeepSeekCredential
from app.settings.schemas import DeepSeekConnectionResult, DeepSeekSettingsResult
from app.settings.security import CredentialCipher, SecretStorageError, SecretTransportError, TransportKeyPair


class DeepSeekSettingsService:
    def __init__(
        self,
        *,
        db: Session,
        workspace_id: uuid.UUID,
        settings: Settings,
        cipher: CredentialCipher,
        transport: TransportKeyPair,
        client_factory: Callable[[str], AIClient] | None = None,
    ) -> None:
        self._db = db
        self._workspace_id = workspace_id
        self._settings = settings
        self._cipher = cipher
        self._transport = transport
        self._client_factory = client_factory or self._build_test_client

    def configured(self) -> tuple[bool, str | None]:
        credential = self._credential()
        return credential is not None, credential.masked_hint if credential else None

    def public_key(self) -> str:
        return self._transport.public_key_pem()

    def save(self, encrypted_api_key: str) -> DeepSeekSettingsResult:
        api_key = self._transport.decrypt(encrypted_api_key)
        encrypted_secret = self._cipher.encrypt(api_key)
        credential = self._credential() or DeepSeekCredential(workspace_id=self._workspace_id)
        credential.encrypted_secret = encrypted_secret
        credential.masked_hint = self._masked_hint(api_key)
        credential.algorithm = self._cipher.algorithm
        self._db.add(credential)
        self._db.commit()
        return DeepSeekSettingsResult(
            configured=True,
            masked_hint=credential.masked_hint,
            message="DeepSeek API Key 已安全保存",
        )

    def read_api_key(self) -> str | None:
        credential = self._credential()
        if credential is None:
            return None
        return self._cipher.decrypt(credential.encrypted_secret)

    async def test_connection(self, encrypted_api_key: str | None) -> DeepSeekConnectionResult:
        try:
            api_key = self._transport.decrypt(encrypted_api_key) if encrypted_api_key else self.read_api_key()
        except (SecretStorageError, SecretTransportError) as exc:
            return DeepSeekConnectionResult(success=False, message=str(exc))
        if not api_key:
            return DeepSeekConnectionResult(success=False, message="尚未配置 DeepSeek API Key")
        try:
            await self._client_factory(api_key).generate_completion(
                system_prompt='只返回 JSON，例如 {"ok": true}。',
                user_prompt='返回 {"ok": true}，用于最小连接测试。',
            )
            return DeepSeekConnectionResult(success=True, message="连接成功，DeepSeek API 可用")
        except AIRequestError as exc:
            return DeepSeekConnectionResult(success=False, message=self._connection_error_message(exc))

    def _credential(self) -> DeepSeekCredential | None:
        return self._db.scalar(
            select(DeepSeekCredential).where(DeepSeekCredential.workspace_id == self._workspace_id)
        )

    def _build_test_client(self, api_key: str) -> AIClient:
        return DeepSeekClient(
            api_key=api_key,
            base_url=self._settings.deepseek_base_url,
            model=self._settings.deepseek_model,
            timeout_seconds=min(self._settings.ai_timeout_seconds, 30.0),
            max_output_tokens=32,
            temperature=0.0,
        )

    @staticmethod
    def _masked_hint(api_key: str) -> str | None:
        normalized = api_key.strip()
        return f"••••{normalized[-4:]}" if len(normalized) >= 4 else None

    @staticmethod
    def _connection_error_message(exc: AIRequestError) -> str:
        detail = str(exc)
        if "401" in detail:
            return "连接失败：API Key 无效"
        if "402" in detail:
            return "连接失败：DeepSeek 账户余额不足"
        if "rate limit" in detail.casefold() or "429" in detail:
            return "连接失败：请求过于频繁，请稍后重试"
        if "timed out" in detail.casefold():
            return "连接失败：DeepSeek 请求超时"
        return "连接失败：无法访问 DeepSeek 服务"
