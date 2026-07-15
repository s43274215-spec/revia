import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import WorkspaceId
from app.models.content import BulletPoint, Chapter, KnowledgePoint
from app.models.project import Project
from sqlalchemy import select
from app.schemas.content import BulletPointRead, BulletPointUpdate, LearningMaterialRead
from app.services.learning_material import LearningMaterialNotFoundError, LearningMaterialService

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/projects/{project_id}/learning-material", response_model=LearningMaterialRead)
def get_learning_material(project_id: uuid.UUID, workspace_id: WorkspaceId, db: DbSession) -> LearningMaterialRead:
    try:
        return LearningMaterialService(db).get(workspace_id, project_id)
    except LearningMaterialNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/bullet-points/{bullet_point_id}", response_model=BulletPointRead)
def update_bullet_point(
    bullet_point_id: uuid.UUID,
    payload: BulletPointUpdate,
    workspace_id: WorkspaceId,
    db: DbSession,
) -> BulletPointRead:
    if not _bullet_belongs_to_workspace(db, workspace_id, bullet_point_id):
        raise HTTPException(status_code=404, detail="Bullet point was not found")
    raise HTTPException(status_code=501, detail="Bullet point update is not implemented")


@router.delete("/bullet-points/{bullet_point_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bullet_point(bullet_point_id: uuid.UUID, workspace_id: WorkspaceId, db: DbSession) -> Response:
    if not _bullet_belongs_to_workspace(db, workspace_id, bullet_point_id):
        raise HTTPException(status_code=404, detail="Bullet point was not found")
    raise HTTPException(status_code=501, detail="Bullet point deletion is not implemented")


def _bullet_belongs_to_workspace(db: Session, workspace_id: uuid.UUID, bullet_point_id: uuid.UUID) -> bool:
    return db.scalar(
        select(BulletPoint.id)
        .join(KnowledgePoint, BulletPoint.knowledge_point_id == KnowledgePoint.id)
        .join(Chapter, KnowledgePoint.chapter_id == Chapter.id)
        .join(Project, Chapter.project_id == Project.id)
        .where(BulletPoint.id == bullet_point_id, Project.workspace_id == workspace_id)
    ) is not None
