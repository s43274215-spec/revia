import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import WorkspaceId
from app.auth.schemas import AccessCodeRequest, WorkspaceRead, WorkspaceSessionRead
from app.auth.security import SessionTokenSigner
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.workspace import Workspace

router = APIRouter()


@router.post("/access", response_model=WorkspaceSessionRead)
def create_workspace_session(
    payload: AccessCodeRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> WorkspaceSessionRead:
    if not hmac.compare_digest(payload.access_code, settings.app_access_code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问码不正确")
    workspace = Workspace(id=uuid.uuid4())
    db.add(workspace)
    db.commit()
    token = SessionTokenSigner(settings.session_signing_key).issue(workspace.id)
    return WorkspaceSessionRead(token=token, workspace_id=workspace.id)


@router.get("/session", response_model=WorkspaceRead)
def get_workspace_session(workspace_id: WorkspaceId) -> WorkspaceRead:
    return WorkspaceRead(workspace_id=workspace_id)
