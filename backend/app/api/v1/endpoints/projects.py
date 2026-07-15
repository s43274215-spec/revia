import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import WorkspaceId
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from app.services.projects import ProjectNotFoundError, ProjectService

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[ProjectRead])
def list_projects(workspace_id: WorkspaceId, db: DbSession) -> list[ProjectRead]:
    return [ProjectRead.model_validate(project) for project in ProjectService(db).list(workspace_id)]


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, workspace_id: WorkspaceId, db: DbSession) -> ProjectRead:
    return ProjectRead.model_validate(ProjectService(db).create(workspace_id, payload))


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: uuid.UUID, workspace_id: WorkspaceId, db: DbSession) -> ProjectRead:
    try:
        return ProjectRead.model_validate(ProjectService(db).get(workspace_id, project_id))
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(project_id: uuid.UUID, payload: ProjectUpdate, workspace_id: WorkspaceId, db: DbSession) -> ProjectRead:
    try:
        return ProjectRead.model_validate(ProjectService(db).update(workspace_id, project_id, payload))
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: uuid.UUID, workspace_id: WorkspaceId, db: DbSession) -> Response:
    try:
        ProjectService(db).delete(workspace_id, project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
