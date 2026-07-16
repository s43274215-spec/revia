import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import DocumentProcessingStatus
from app.models.project import Document, Project
from app.schemas.project import ProjectCreate, ProjectUpdate


class ProjectNotFoundError(LookupError):
    pass


class ProjectService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list(self, workspace_id: uuid.UUID) -> list[Project]:
        statement = select(Project).where(Project.workspace_id == workspace_id).order_by(Project.created_at.desc())
        return list(self._db.scalars(statement).all())

    def active_document(self, workspace_id: uuid.UUID) -> tuple[Document, str] | None:
        row = self._db.execute(
            select(Document, Project.name)
            .join(Project, Document.project_id == Project.id)
            .where(
                Project.workspace_id == workspace_id,
                Document.processing_status.in_([
                    DocumentProcessingStatus.QUEUED,
                    DocumentProcessingStatus.PROCESSING,
                    DocumentProcessingStatus.PARSING,
                    DocumentProcessingStatus.INTERRUPTED,
                ]),
            )
            .order_by(Document.created_at.asc())
            .limit(1)
        ).first()
        if row is None:
            return None
        return row[0], row[1]

    def create(self, workspace_id: uuid.UUID, payload: ProjectCreate) -> Project:
        project = Project(workspace_id=workspace_id, name=payload.name.strip(), description=payload.description)
        self._db.add(project)
        self._db.commit()
        self._db.refresh(project)
        return project

    def get(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        project = self._db.scalar(select(Project).where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        ))
        if project is None:
            raise ProjectNotFoundError(f"Project {project_id} was not found")
        return project

    def update(self, workspace_id: uuid.UUID, project_id: uuid.UUID, payload: ProjectUpdate) -> Project:
        project = self.get(workspace_id, project_id)
        changes = payload.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"] is not None:
            changes["name"] = changes["name"].strip()
        for field, value in changes.items():
            setattr(project, field, value)
        self._db.commit()
        self._db.refresh(project)
        return project

    def delete(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        *,
        delete_object: Callable[[str], None] | None = None,
    ) -> None:
        project = self.get(workspace_id, project_id)
        if delete_object is not None:
            for document in project.documents:
                if not document.storage_key:
                    continue
                try:
                    delete_object(document.storage_key)
                except Exception:
                    # Database deletion must not be blocked by best-effort object cleanup.
                    continue
        self._db.delete(project)
        self._db.commit()
