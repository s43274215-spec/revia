import uuid

from app.auth.security import SessionTokenSigner
from app.core.config import Settings


TEST_SETTINGS = Settings(_env_file=None)


def authorization_header(
    workspace_id: uuid.UUID,
    settings: Settings = TEST_SETTINGS,
    role: str = "public",
) -> dict[str, str]:
    token = SessionTokenSigner(settings.session_signing_key).issue(workspace_id, role)
    return {"Authorization": f"Bearer {token}"}
