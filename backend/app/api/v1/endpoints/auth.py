import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import CurrentWorkspace
from app.auth.schemas import AccessCodeRequest, AccessModeRead, WorkspaceRead, WorkspaceSessionRead
from app.auth.security import SessionTokenSigner
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.enums import WorkspaceRole
from app.models.workspace import Workspace
from app.settings.site_service import SiteSettingsService

router = APIRouter()


def _issue_workspace_session(workspace: Workspace, settings: Settings) -> WorkspaceSessionRead:
    token = SessionTokenSigner(settings.session_signing_key).issue(workspace.id, workspace.role)
    return WorkspaceSessionRead(token=token, workspace_id=workspace.id, role=workspace.role)


@router.get("/mode", response_model=AccessModeRead)
def get_access_mode(
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> AccessModeRead:
    site = SiteSettingsService(db, settings).get()
    return AccessModeRead(public_access_enabled=site.public_access_enabled)


@router.post("/anonymous", response_model=WorkspaceSessionRead)
def create_anonymous_workspace_session(
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> WorkspaceSessionRead:
    site = SiteSettingsService(db, settings).get()
    if not site.public_access_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Revia 当前暂未开放，请稍后再试。")
    workspace = Workspace(id=uuid.uuid4(), role=WorkspaceRole.PUBLIC)
    db.add(workspace)
    db.commit()
    return _issue_workspace_session(workspace, settings)


@router.post("/access", response_model=WorkspaceSessionRead)
def create_workspace_session(
    payload: AccessCodeRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> WorkspaceSessionRead:
    if not hmac.compare_digest(payload.access_code, settings.app_access_code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问码无效")
    owner = SiteSettingsService(db, settings).owner_workspace()
    return _issue_workspace_session(owner, settings)


@router.get("/session", response_model=WorkspaceRead)
def get_workspace_session(workspace: CurrentWorkspace) -> WorkspaceRead:
    return WorkspaceRead(workspace_id=workspace.id, role=workspace.role)
