import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import WorkspaceId
from app.auth.schemas import AccessCodeRequest, AccessModeRead, WorkspaceRead, WorkspaceSessionRead
from app.auth.security import SessionTokenSigner
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.workspace import Workspace

router = APIRouter()


def _issue_workspace_session(db: Session, settings: Settings) -> WorkspaceSessionRead:
    workspace = Workspace(id=uuid.uuid4())
    db.add(workspace)
    db.commit()
    token = SessionTokenSigner(settings.session_signing_key).issue(workspace.id)
    return WorkspaceSessionRead(token=token, workspace_id=workspace.id)


@router.get("/mode", response_model=AccessModeRead)
def get_access_mode(settings: Annotated[Settings, Depends(get_settings)]) -> AccessModeRead:
    return AccessModeRead(public_access_enabled=settings.public_access_enabled)


@router.post("/anonymous", response_model=WorkspaceSessionRead)
def create_anonymous_workspace_session(
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> WorkspaceSessionRead:
    if not settings.public_access_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="公开访问当前未启用")
    return _issue_workspace_session(db, settings)


@router.post("/access", response_model=WorkspaceSessionRead)
def create_workspace_session(
    payload: AccessCodeRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> WorkspaceSessionRead:
    if not hmac.compare_digest(payload.access_code, settings.app_access_code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问码不正确")
    return _issue_workspace_session(db, settings)


@router.get("/session", response_model=WorkspaceRead)
def get_workspace_session(workspace_id: WorkspaceId) -> WorkspaceRead:
    return WorkspaceRead(workspace_id=workspace_id)
