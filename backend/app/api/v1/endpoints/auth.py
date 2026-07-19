import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import CurrentWorkspace, SESSION_COOKIE_NAME
from app.auth.schemas import AccessCodeRequest, AccessModeRead, WorkspaceRead, WorkspaceSessionRead
from app.auth.security import SessionTokenSigner
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.enums import WorkspaceRole
from app.models.workspace import Workspace
from app.settings.site_service import SiteSettingsService

router = APIRouter()


def _issue_workspace_session(
    workspace: Workspace,
    access_mode: str,
    settings: Settings,
    response: Response,
) -> WorkspaceSessionRead:
    token = SessionTokenSigner(settings.session_signing_key).issue(
        workspace.id,
        access_mode,
        settings.session_max_age_seconds,
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.environment.casefold() == "production",
        samesite="lax",
        path="/",
    )
    return WorkspaceSessionRead(workspace_id=workspace.id, role=access_mode)


@router.get("/mode", response_model=AccessModeRead)
def get_access_mode(
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> AccessModeRead:
    site = SiteSettingsService(db, settings).get()
    return AccessModeRead(
        public_access_enabled=site.public_access_enabled,
        demo_access_enabled=bool(settings.demo_access_code and settings.demo_workspace_id),
    )


@router.post("/anonymous", response_model=WorkspaceSessionRead)
def create_anonymous_workspace_session(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> WorkspaceSessionRead:
    site = SiteSettingsService(db, settings).get()
    if not site.public_access_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Revia 当前暂未开放，请稍后再试。")
    workspace = Workspace(id=uuid.uuid4(), role=WorkspaceRole.PUBLIC)
    db.add(workspace)
    db.commit()
    return _issue_workspace_session(workspace, "public", settings, response)


@router.post("/access", response_model=WorkspaceSessionRead)
def create_workspace_session(
    payload: AccessCodeRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> WorkspaceSessionRead:
    if hmac.compare_digest(payload.access_code, settings.effective_owner_access_code):
        if settings.owner_workspace_id is None:
            owner = SiteSettingsService(db, settings).owner_workspace()
        else:
            owner = db.scalar(select(Workspace).where(
                Workspace.id == settings.owner_workspace_id,
                Workspace.role == WorkspaceRole.OWNER,
            ))
            if owner is None:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="站长工作区尚未正确配置")
        return _issue_workspace_session(owner, "owner", settings, response)
    if settings.demo_access_code and hmac.compare_digest(payload.access_code, settings.demo_access_code):
        demo = db.get(Workspace, settings.demo_workspace_id)
        if demo is None or demo.role == WorkspaceRole.OWNER:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="演示工作区尚未正确配置")
        return _issue_workspace_session(demo, "demo", settings, response)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问码无效")


@router.get("/session", response_model=WorkspaceRead)
def get_workspace_session(workspace: CurrentWorkspace) -> WorkspaceRead:
    return WorkspaceRead(workspace_id=workspace.id, role=workspace.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_workspace_session(response: Response) -> Response:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", httponly=True, samesite="lax")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
