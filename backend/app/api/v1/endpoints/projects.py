import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import WritableWorkspaceId, WorkspaceId
from app.core.config import Settings, get_settings
from app.schemas.project import ActiveDocumentRead, ProjectCreate, ProjectRead, ProjectUpdate
from app.services.projects import ProjectDeletionError, ProjectNotFoundError, ProjectService
from app.services.storage import build_storage_provider

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[ProjectRead])
def list_projects(workspace_id: WorkspaceId, db: DbSession) -> list[ProjectRead]:
    return [ProjectRead.model_validate(project) for project in ProjectService(db).list(workspace_id)]


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, workspace_id: WritableWorkspaceId, db: DbSession) -> ProjectRead:
    return ProjectRead.model_validate(ProjectService(db).create(workspace_id, payload))


@router.get("/active-document", response_model=ActiveDocumentRead | None)
def get_active_document(workspace_id: WorkspaceId, db: DbSession) -> ActiveDocumentRead | None:
    active = ProjectService(db).active_document(workspace_id)
    if active is None:
        return None
    document, project_name = active
    return ActiveDocumentRead(
        document_id=document.id,
        project_id=document.project_id,
        filename=document.original_name,
        project_name=project_name,
        processing_status=document.processing_status,
        processing_phase=document.processing_phase,
        current_page=document.current_page,
        total_pages=document.total_pages,
        processed_pages=document.processed_pages,
        error_message=document.error_message,
    )


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: uuid.UUID, workspace_id: WorkspaceId, db: DbSession) -> ProjectRead:
    try:
        return ProjectRead.model_validate(ProjectService(db).get(workspace_id, project_id))
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(project_id: uuid.UUID, payload: ProjectUpdate, workspace_id: WritableWorkspaceId, db: DbSession) -> ProjectRead:
    try:
        return ProjectRead.model_validate(ProjectService(db).update(workspace_id, project_id, payload))
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID,
    workspace_id: WritableWorkspaceId,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    try:
        ProjectService(db).delete(
            workspace_id,
            project_id,
            delete_object=build_storage_provider(settings).delete_object,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProjectDeletionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
