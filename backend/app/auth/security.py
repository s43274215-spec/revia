import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime


class SessionTokenError(ValueError):
    pass


class SessionTokenSigner:
    def __init__(self, signing_key: str) -> None:
        key = signing_key.encode("utf-8")
        if len(key) < 32:
            raise ValueError("SESSION_SIGNING_KEY must contain at least 32 bytes")
        self._key = key

    def issue(self, workspace_id: uuid.UUID) -> str:
        payload = {
            "version": 1,
            "workspace_id": str(workspace_id),
            "issued_at": datetime.now(UTC).isoformat(),
        }
        encoded = self._encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = self._sign(encoded)
        return f"{encoded}.{signature}"

    def verify(self, token: str) -> uuid.UUID:
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected_signature = self._sign(encoded)
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise SessionTokenError("Invalid workspace token signature")
            payload = json.loads(self._decode(encoded))
            if payload.get("version") != 1:
                raise SessionTokenError("Unsupported workspace token version")
            return uuid.UUID(str(payload["workspace_id"]))
        except SessionTokenError:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SessionTokenError("Invalid workspace token") from exc

    def _sign(self, encoded_payload: str) -> str:
        digest = hmac.new(self._key, encoded_payload.encode("ascii"), hashlib.sha256).digest()
        return self._encode(digest)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
