from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.dependencies import WorkspaceId
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.document.parser import PDFParser
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.models.enums import DocumentKind, DocumentProcessingStatus
from app.models.project import Document, Project
from app.schemas.document import (
    DocumentProcessingRead,
    DocumentProgressRead,
    DocumentUploadCreate,
    DocumentUploadTargetRead,
    ParsedDocumentRead,
)
from app.schemas.project import DocumentRead, SyllabusRead, SyllabusUpsert
from app.services.document_processing import (
    DocumentNotFoundError,
    DocumentProcessingError,
    DocumentProcessingService,
    DocumentProjectNotFoundError,
    DocumentTaskRunner,
)
from app.services.storage import (
    LocalStorageProvider,
    StorageError,
    UploadAuthorizationError,
    UploadLimitError,
    build_storage_provider,
)
from app.services.syllabus import SyllabusDocumentError, SyllabusProjectNotFoundError, SyllabusService

router = APIRouter()


def build_document_processing_service(db: Session, settings: Settings) -> DocumentProcessingService:
    return DocumentProcessingService(
        db=db,
        storage=build_storage_provider(settings),
        parser=PDFParser(
            ocr_enabled=settings.ocr_enabled,
            ocr_dpi=settings.ocr_dpi,
            minimum_text_length=settings.ocr_minimum_text_length,
            max_pages=settings.max_pdf_pages,
        ),
        structurer=TextStructurer(),
        splitter=StructuredTextSplitter(),
        max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
        upload_url_expires_seconds=settings.upload_url_expires_seconds,
        lease_seconds=settings.document_lease_seconds,
    )


def get_document_processing_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentProcessingService:
    return build_document_processing_service(db, settings)


def get_document_task_runner(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentTaskRunner:
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    return DocumentTaskRunner(factory, lambda session: build_document_processing_service(session, settings))


DocumentService = Annotated[DocumentProcessingService, Depends(get_document_processing_service)]
DocumentRunner = Annotated[DocumentTaskRunner, Depends(get_document_task_runner)]


@router.post(
    "/{project_id}/documents/uploads",
    response_model=DocumentUploadTargetRead,
    status_code=status.HTTP_201_CREATED,
)
def create_document_upload(
    project_id: uuid.UUID,
    payload: DocumentUploadCreate,
    request: Request,
    workspace_id: WorkspaceId,
    settings: Annotated[Settings, Depends(get_settings)],
    service: DocumentService,
) -> DocumentUploadTargetRead:
    local_upload_template = (
        f"{str(request.base_url).rstrip('/')}{settings.api_v1_prefix}/projects/"
        f"{project_id}/documents/{{document_id}}/content"
    )
    try:
        document, target = service.create_upload(
            workspace_id,
            project_id,
            payload.kind,
            filename=payload.filename,
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
            upload_endpoint=local_upload_template if settings.storage_backend == "local" else None,
        )
    except (DocumentProjectNotFoundError, DocumentNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (DocumentProcessingError, StorageError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return DocumentUploadTargetRead(
        document=DocumentRead.model_validate(document),
        upload_url=target.url,
        method=target.method,
        headers=target.headers,
        expires_at=target.expires_at,
    )


@router.put(
    "/{project_id}/documents/{document_id}/content",
    name="upload_local_document",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def upload_local_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    token: Annotated[str, Query(min_length=1)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    storage = build_storage_provider(settings)
    if not isinstance(storage, LocalStorageProvider):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="本地上传入口未启用")
    try:
        workspace_id, signed_document_id, object_key = storage.signer.verify(token)
        if signed_document_id != document_id:
            raise UploadAuthorizationError("上传地址与文档不一致")
        document = DocumentProcessingService(
            db,
            storage,
            PDFParser(max_pages=settings.max_pdf_pages),
            TextStructurer(),
            StructuredTextSplitter(),
            max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
        ).get_document(workspace_id, project_id, document_id)
        if document.storage_key != object_key:
            raise UploadAuthorizationError("上传对象与文档不一致")
        if request.headers.get("content-type", "").split(";", 1)[0].casefold() != "application/pdf":
            raise DocumentProcessingError("文件 MIME 类型必须为 application/pdf")
        await storage.save_stream(object_key, request.stream())
    except UploadAuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (DocumentProcessingError, UploadLimitError, StorageError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/syllabus", response_model=SyllabusRead | None)
def get_syllabus(
    project_id: uuid.UUID,
    workspace_id: WorkspaceId,
    db: Annotated[Session, Depends(get_db)],
) -> SyllabusRead | None:
    try:
        syllabus = SyllabusService(db).get(workspace_id, project_id)
    except SyllabusProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SyllabusRead.model_validate(syllabus) if syllabus is not None else None


@router.post(
    "/{project_id}/documents/{document_id}/confirm",
    response_model=DocumentProgressRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def confirm_document_upload(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    workspace_id: WorkspaceId,
    service: DocumentService,
    runner: DocumentRunner,
    background_tasks: BackgroundTasks,
) -> DocumentProgressRead:
    try:
        document = service.confirm_upload(workspace_id, project_id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (DocumentProcessingError, StorageError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if document.processing_status != DocumentProcessingStatus.PARSED:
        background_tasks.add_task(runner.run, document.id)
    return _progress(document)


@router.get("/{project_id}/documents/latest", response_model=DocumentProgressRead | None)
def get_latest_document(
    project_id: uuid.UUID,
    kind: DocumentKind,
    workspace_id: WorkspaceId,
    service: DocumentService,
    runner: DocumentRunner,
    background_tasks: BackgroundTasks,
) -> DocumentProgressRead | None:
    try:
        document = service.latest_document(workspace_id, project_id, kind)
    except DocumentProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if document is None:
        return None
    if document.processing_status == DocumentProcessingStatus.UPLOADED:
        try:
            document = service.confirm_upload(workspace_id, project_id, document.id)
        except (DocumentProcessingError, StorageError):
            pass
    _schedule_resume(document, runner, background_tasks)
    return _progress(document)


@router.get("/{project_id}/documents/{document_id}", response_model=DocumentProgressRead)
def get_document_progress(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    workspace_id: WorkspaceId,
    service: DocumentService,
    runner: DocumentRunner,
    background_tasks: BackgroundTasks,
) -> DocumentProgressRead:
    try:
        document = service.get_document(workspace_id, project_id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _schedule_resume(document, runner, background_tasks)
    return _progress(document)


@router.post("/{project_id}/document-cleanup/expired")
def cleanup_expired_documents(
    project_id: uuid.UUID,
    workspace_id: WorkspaceId,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    older_than_hours: Annotated[int, Query(ge=1, le=24 * 30)] = 72,
) -> dict[str, int]:
    project = db.scalar(select(Project).where(Project.id == project_id, Project.workspace_id == workspace_id))
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    storage = build_storage_provider(settings)
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    documents = list(db.scalars(
        select(Document).where(
            Document.project_id == project_id,
            Document.storage_key.is_not(None),
            Document.created_at < cutoff,
        )
    ).all())
    deleted = 0
    for document in documents:
        assert document.storage_key is not None
        try:
            storage.delete_object(document.storage_key)
            document.storage_key = None
            if document.processing_status != DocumentProcessingStatus.PARSED:
                document.processing_status = DocumentProcessingStatus.FAILED
                document.processing_phase = "failed"
                document.error_message = "过期的未完成 PDF 已清理，请重新上传"
            deleted += 1
        except Exception:
            continue
    db.commit()
    return {"deleted": deleted}


@router.post(
    "/{project_id}/documents",
    response_model=DocumentProcessingRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document_legacy(
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
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="解析结果未保存")
    return DocumentProcessingRead(
        document=DocumentRead.model_validate(document),
        parsed_document=ParsedDocumentRead.model_validate(document.parsed_document),
    )


@router.put(
    "/{project_id}/syllabus",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def upsert_syllabus(
    project_id: uuid.UUID,
    payload: SyllabusUpsert,
    workspace_id: WorkspaceId,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        SyllabusService(db).upsert(workspace_id, project_id, payload)
    except SyllabusProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SyllabusDocumentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _schedule_resume(document: Document, runner: DocumentTaskRunner, background_tasks: BackgroundTasks) -> None:
    if document.processing_status in {
        DocumentProcessingStatus.QUEUED,
        DocumentProcessingStatus.PROCESSING,
        DocumentProcessingStatus.PARSING,
        DocumentProcessingStatus.INTERRUPTED,
    }:
        background_tasks.add_task(runner.run, document.id)


def _progress(document: Document) -> DocumentProgressRead:
    payload = DocumentProgressRead.model_validate(document)
    payload.is_resuming = document.processing_phase == "resuming" or (
        document.processed_pages > 0
        and document.processing_status in {
            DocumentProcessingStatus.QUEUED,
            DocumentProcessingStatus.PROCESSING,
            DocumentProcessingStatus.INTERRUPTED,
        }
    )
    return payload
