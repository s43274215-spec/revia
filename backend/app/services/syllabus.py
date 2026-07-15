import uuid

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.document import ParsedDocument
from app.models.project import Document, Project, Syllabus
from app.schemas.project import SyllabusUpsert


class SyllabusProjectNotFoundError(LookupError):
    pass


class SyllabusDocumentError(ValueError):
    pass


class SyllabusService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def upsert(self, workspace_id: uuid.UUID, project_id: uuid.UUID, payload: SyllabusUpsert) -> Syllabus:
        project = self._db.scalar(select(Project).where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        ))
        if project is None:
            raise SyllabusProjectNotFoundError(f"Project {project_id} was not found")
        if payload.document_id is not None:
            document = self._db.scalar(
                select(Document)
                .join(Project, Document.project_id == Project.id)
                .where(
                    Document.id == payload.document_id,
                    Document.project_id == project_id,
                    Project.workspace_id == workspace_id,
                )
            )
            if document is None:
                raise SyllabusDocumentError("Syllabus document does not belong to this project")
        syllabus_text = payload.text
        if payload.document_id is not None and not (syllabus_text or "").strip():
            parsed_document = self._db.query(ParsedDocument).filter_by(document_id=payload.document_id).one_or_none()
            if parsed_document is None:
                raise SyllabusDocumentError("Syllabus PDF has not been parsed")
            syllabus_text = parsed_document.raw_text
        syllabus = project.syllabus or Syllabus(project_id=project_id)
        syllabus.text = syllabus_text
        syllabus.document_id = payload.document_id
        self._db.add(syllabus)
        self._db.commit()
        self._db.refresh(syllabus)
        return syllabus
