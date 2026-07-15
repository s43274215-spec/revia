import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


class SessionTokenError(ValueError):
    pass


@dataclass(frozen=True)
class SessionTokenClaims:
    workspace_id: uuid.UUID
    role: str


class SessionTokenSigner:
    def __init__(self, signing_key: str) -> None:
        key = signing_key.encode("utf-8")
        if len(key) < 32:
            raise ValueError("SESSION_SIGNING_KEY must contain at least 32 bytes")
        self._key = key

    def issue(self, workspace_id: uuid.UUID, role: str = "public") -> str:
        payload = {
            "version": 2,
            "workspace_id": str(workspace_id),
            "role": role,
            "issued_at": datetime.now(UTC).isoformat(),
        }
        encoded = self._encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = self._sign(encoded)
        return f"{encoded}.{signature}"

    def verify(self, token: str) -> SessionTokenClaims:
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected_signature = self._sign(encoded)
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise SessionTokenError("Invalid workspace token signature")
            payload = json.loads(self._decode(encoded))
            version = payload.get("version")
            if version not in {1, 2}:
                raise SessionTokenError("Unsupported workspace token version")
            role = "public" if version == 1 else str(payload["role"])
            if role not in {"owner", "public"}:
                raise SessionTokenError("Invalid workspace role")
            return SessionTokenClaims(workspace_id=uuid.UUID(str(payload["workspace_id"])), role=role)
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
