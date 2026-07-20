import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.enums import (
    DocumentProcessingStatus,
    GenerationStatus,
    ProjectStatus,
)
from app.models.project import Document, GenerationJob, Project
from app.schemas.project import ProjectCreate, ProjectUpdate


_ACTIVE_DOCUMENT_STATUSES = (
    DocumentProcessingStatus.QUEUED,
    DocumentProcessingStatus.PROCESSING,
    DocumentProcessingStatus.PARSING,
    DocumentProcessingStatus.INTERRUPTED,
)
_ACTIVE_GENERATION_STATUSES = (
    GenerationStatus.PENDING,
    GenerationStatus.PARSING,
    GenerationStatus.MATCHING,
    GenerationStatus.GENERATING,
    GenerationStatus.VALIDATING,
)


class ProjectNotFoundError(LookupError):
    pass


class ProjectDeletionError(RuntimeError):
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
                Document.processing_status.in_(_ACTIVE_DOCUMENT_STATUSES),
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
        project = self._locked_project(workspace_id, project_id)
        now = datetime.now(UTC)
        storage_objects = [
            (document.original_name, document.storage_key)
            for document in project.documents
            if document.storage_key
        ]

        # First make all durable workers observe a terminal state. This commit is
        # intentionally separate from the final cascade delete: a document parser
        # or generation worker may currently be between checkpoints, and must stop
        # before storage objects disappear underneath it.
        for document in project.documents:
            if document.processing_status not in _ACTIVE_DOCUMENT_STATUSES:
                continue
            document.processing_status = DocumentProcessingStatus.CANCELLED
            document.processing_phase = "project_deleted"
            document.cancelled_at = now
            document.retry_not_before = None
            document.lease_owner = None
            document.lease_expires_at = None
            document.error_message = "项目删除请求已停止文档处理任务"

        for job in project.generation_jobs:
            if job.status not in _ACTIVE_GENERATION_STATUSES:
                continue
            job.status = GenerationStatus.FAILED
            history = list(job.status_history or [])
            if not history or history[-1] != GenerationStatus.FAILED.value:
                history.append(GenerationStatus.FAILED.value)
            job.status_history = history
            job.progress = 100
            job.error_message = "project_deleted: generation stopped because the project was deleted"
            job.completed_at = now
            job.last_activity_at = now

        if project.status == ProjectStatus.PROCESSING:
            project.status = ProjectStatus.COMPLETED if project.chapters else ProjectStatus.FAILED

        try:
            self._db.commit()
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise ProjectDeletionError("无法停止项目后台任务，请稍后重试") from exc

        storage_failures: list[str] = []
        if delete_object is not None:
            for original_name, object_key in storage_objects:
                try:
                    delete_object(object_key)
                except Exception:
                    storage_failures.append(original_name)
        if storage_failures:
            names = "、".join(storage_failures[:3])
            suffix = "等文件" if len(storage_failures) > 3 else ""
            raise ProjectDeletionError(
                f"项目任务已停止，但无法删除上传文件：{names}{suffix}。请稍后再次删除项目。"
            )

        project = self._locked_project(workspace_id, project_id)
        try:
            self._db.delete(project)
            self._db.commit()
        except SQLAlchemyError as exc:
            self._db.rollback()
            raise ProjectDeletionError("上传文件已清理，但项目数据库记录删除失败，请再次尝试删除") from exc

    def _locked_project(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        project = self._db.scalar(
            select(Project)
            .where(Project.id == project_id, Project.workspace_id == workspace_id)
            .with_for_update()
        )
        if project is None:
            raise ProjectNotFoundError(f"Project {project_id} was not found")
        return project
