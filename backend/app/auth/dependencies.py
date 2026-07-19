import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.security import SessionTokenError, SessionTokenSigner
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.enums import WorkspaceRole
from app.models.workspace import Workspace
from app.settings.site_service import SiteSettingsService

bearer = HTTPBearer(auto_error=False)
SESSION_COOKIE_NAME = "revia_session"


@dataclass(frozen=True)
class AuthenticatedWorkspace:
    workspace: Workspace
    access_mode: str

    @property
    def id(self) -> uuid.UUID:
        return self.workspace.id

    @property
    def role(self) -> str:
        return self.access_mode


def get_current_workspace(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> AuthenticatedWorkspace:
    token = credentials.credentials if credentials is not None and credentials.scheme.casefold() == "bearer" else session_cookie
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要工作区授权")
    try:
        claims = SessionTokenSigner(settings.session_signing_key).verify(token)
    except SessionTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="工作区凭证无效，请重新授权") from exc
    workspace = db.get(Workspace, claims.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="工作区凭证无效，请重新授权")
    if claims.role == "owner":
        if workspace.role != WorkspaceRole.OWNER or (
            settings.owner_workspace_id is not None and workspace.id != settings.owner_workspace_id
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="工作区凭证无效，请重新授权")
    elif claims.role == "demo":
        if settings.demo_workspace_id is None or workspace.id != settings.demo_workspace_id or workspace.role == WorkspaceRole.OWNER:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="工作区凭证无效，请重新授权")
    elif workspace.role != WorkspaceRole.PUBLIC:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="工作区凭证无效，请重新授权")
    if claims.role == "public":
        site = SiteSettingsService(db, settings).get()
        if not site.public_access_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Revia 当前暂未开放，请稍后再试。",
            )
    return AuthenticatedWorkspace(workspace=workspace, access_mode=claims.role)


def get_current_workspace_id(workspace: Annotated[AuthenticatedWorkspace, Depends(get_current_workspace)]) -> uuid.UUID:
    return workspace.id


def get_writable_workspace_id(workspace: Annotated[AuthenticatedWorkspace, Depends(get_current_workspace)]) -> uuid.UUID:
    if workspace.access_mode == "demo":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="演示模式不会保存修改")
    return workspace.id


def get_owner_workspace(workspace: Annotated[AuthenticatedWorkspace, Depends(get_current_workspace)]) -> Workspace:
    if workspace.access_mode != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅站长可以执行此操作")
    return workspace.workspace


CurrentWorkspace = Annotated[AuthenticatedWorkspace, Depends(get_current_workspace)]
OwnerWorkspace = Annotated[Workspace, Depends(get_owner_workspace)]
WorkspaceId = Annotated[uuid.UUID, Depends(get_current_workspace_id)]
WritableWorkspaceId = Annotated[uuid.UUID, Depends(get_writable_workspace_id)]
