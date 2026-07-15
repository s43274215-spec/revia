import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import Project
from app.schemas.content import LearningMaterialRead


class LearningMaterialNotFoundError(LookupError):
    pass


class LearningMaterialService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> LearningMaterialRead:
        project = self._db.scalar(select(Project).where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        ))
        if project is None:
            raise LearningMaterialNotFoundError(f"Project {project_id} was not found")
        return LearningMaterialRead(project_id=project.id, chapters=project.chapters)
