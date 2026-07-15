import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.security import SessionTokenError, SessionTokenSigner
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.workspace import Workspace

bearer = HTTPBearer(auto_error=False)


def get_current_workspace_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> uuid.UUID:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要工作区授权")
    try:
        workspace_id = SessionTokenSigner(settings.session_signing_key).verify(credentials.credentials)
    except SessionTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="工作区凭证无效，请重新授权") from exc
    if db.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="匿名工作区不存在")
    return workspace_id


WorkspaceId = Annotated[uuid.UUID, Depends(get_current_workspace_id)]
