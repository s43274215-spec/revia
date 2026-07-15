import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.security import SessionTokenError, SessionTokenSigner
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.enums import WorkspaceRole
from app.models.workspace import Workspace
from app.settings.site_service import SiteSettingsService

bearer = HTTPBearer(auto_error=False)


def get_current_workspace(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> Workspace:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要工作区授权")
    try:
        claims = SessionTokenSigner(settings.session_signing_key).verify(credentials.credentials)
    except SessionTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="工作区凭证无效，请重新授权") from exc
    workspace = db.get(Workspace, claims.workspace_id)
    if workspace is None or workspace.role != claims.role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="工作区凭证无效，请重新授权")
    if workspace.role != WorkspaceRole.OWNER:
        site = SiteSettingsService(db, settings).get()
        if not site.public_access_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Revia 当前暂未开放，请稍后再试。",
            )
    return workspace


def get_current_workspace_id(workspace: Annotated[Workspace, Depends(get_current_workspace)]) -> uuid.UUID:
    return workspace.id


def get_owner_workspace(workspace: Annotated[Workspace, Depends(get_current_workspace)]) -> Workspace:
    if workspace.role != WorkspaceRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅站长可以执行此操作")
    return workspace


CurrentWorkspace = Annotated[Workspace, Depends(get_current_workspace)]
OwnerWorkspace = Annotated[Workspace, Depends(get_owner_workspace)]
WorkspaceId = Annotated[uuid.UUID, Depends(get_current_workspace_id)]
