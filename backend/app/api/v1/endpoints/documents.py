import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.auth.dependencies import WorkspaceId
from app.db.session import get_db
from app.document.parser import PDFParser
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.models.enums import DocumentKind
from app.schemas.document import DocumentProcessingRead, ParsedDocumentRead
from app.schemas.project import DocumentRead, SyllabusUpsert
from app.services.document_processing import (
    DocumentProcessingError,
    DocumentProcessingService,
    DocumentProjectNotFoundError,
)
from app.services.storage import LocalFileStorage
from app.services.syllabus import SyllabusDocumentError, SyllabusProjectNotFoundError, SyllabusService

router = APIRouter()


def get_document_processing_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentProcessingService:
    return DocumentProcessingService(
        db=db,
        storage=LocalFileStorage(
            Path(settings.file_storage_root),
            max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
        ),
        parser=PDFParser(
            ocr_enabled=settings.ocr_enabled,
            ocr_dpi=settings.ocr_dpi,
            minimum_text_length=settings.ocr_minimum_text_length,
            max_pages=settings.max_pdf_pages,
        ),
        structurer=TextStructurer(),
        splitter=StructuredTextSplitter(),
    )


DocumentService = Annotated[DocumentProcessingService, Depends(get_document_processing_service)]


@router.post(
    "/{project_id}/documents",
    response_model=DocumentProcessingRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    project_id: uuid.UUID,
    kind: Annotated[DocumentKind, Form()],
    file: Annotated[UploadFile, File()],
    workspace_id: WorkspaceId,
    service: DocumentService,
) -> DocumentProcessingRead:
    try:
        document = await service.process_upload(workspace_id, project_id, kind, file)
    except DocumentProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DocumentProcessingError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if document.parsed_document is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Parsed document was not saved")
    return DocumentProcessingRead(
        document=DocumentRead.model_validate(document),
        parsed_document=ParsedDocumentRead.model_validate(document.parsed_document),
    )


@router.put("/{project_id}/syllabus", status_code=status.HTTP_204_NO_CONTENT)
def upsert_syllabus(
    project_id: uuid.UUID,
    payload: SyllabusUpsert,
    workspace_id: WorkspaceId,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    try:
        SyllabusService(db).upsert(workspace_id, project_id, payload)
    except SyllabusProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SyllabusDocumentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
