from __future__ import annotations

import uuid
from threading import RLock
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

import fitz
from fastapi import UploadFile
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.document.parser import (
    OCRDisabledError,
    OCRResourceLimitedError,
    PDFParser,
    PDFParsingError,
    ParsedPDF,
    ParsedPageData,
)
from app.core.document_memory_diagnostics import DocumentMemoryDiagnostics
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.models.document import DocumentPage, ParsedDocument, ParsedPage, TextChunk
from app.models.enums import (
    DocumentKind,
    DocumentPageStatus,
    DocumentProcessingStatus,
    ExtractionMethod,
    WorkspaceRole,
)
from app.models.project import Document, Project
from app.models.workspace import QuotaGuard, Workspace
from app.services.storage import (
    LocalStorageProvider,
    StorageError,
    StorageProvider,
    UploadLimitError,
    UploadTarget,
    build_object_key,
    validate_object_scope,
)


class DocumentProcessingError(RuntimeError):
    pass


class DocumentQuotaError(DocumentProcessingError):
    pass


class DocumentProjectNotFoundError(LookupError):
    pass


class DocumentNotFoundError(LookupError):
    pass


class DocumentCancellationError(DocumentProcessingError):
    pass


class LeaseUnavailableError(RuntimeError):
    pass


class SimulatedInterruption(RuntimeError):
    pass


ACTIVE_DOCUMENT_STATUSES = (
    DocumentProcessingStatus.QUEUED,
    DocumentProcessingStatus.PROCESSING,
    DocumentProcessingStatus.PARSING,
    DocumentProcessingStatus.INTERRUPTED,
)

QUEUE_DOCUMENT_STATUSES = (
    DocumentProcessingStatus.QUEUED,
    DocumentProcessingStatus.PROCESSING,
    DocumentProcessingStatus.PARSING,
    DocumentProcessingStatus.INTERRUPTED,
)

# SQLite does not implement SELECT FOR UPDATE. This keeps local/test acceptance
# deterministic; PostgreSQL uses the QuotaGuard row lock below.
_quota_lock = RLock()
_OCR_PAGE_RETRY_LIMIT = 3
_OCR_RETRY_BASE_SECONDS = 60
_OCR_RESOURCE_PAUSE_SECONDS = 3600
_OCR_RESOURCE_MESSAGE = "OCR 处理因服务器资源不足暂停，系统稍后可从当前页继续。"
_OCR_DISABLED_REASON = "ocr_disabled"


class DocumentProcessingService:
    def __init__(
        self,
        db: Session,
        storage: StorageProvider,
        parser: PDFParser,
        structurer: TextStructurer,
        splitter: StructuredTextSplitter,
        *,
        max_upload_bytes: int = 150 * 1024 * 1024,
        upload_url_expires_seconds: int = 900,
        lease_seconds: int = 300,
        max_document_retries: int = 3,
        workspace_max_active_documents: int = 1,
        workspace_rolling_24h_page_limit: int = 1200,
        global_max_processing_documents: int = 1,
        global_rolling_24h_page_limit: int = 3000,
        memory_diagnostics_enabled: bool = False,
    ) -> None:
        self._db = db
        self._storage = storage
        self._parser = parser
        self._structurer = structurer
        self._splitter = splitter
        self._max_upload_bytes = max_upload_bytes
        self._upload_url_expires_seconds = upload_url_expires_seconds
        self._lease_seconds = lease_seconds
        self._max_document_retries = max_document_retries
        self._workspace_max_active_documents = workspace_max_active_documents
        self._workspace_rolling_24h_page_limit = workspace_rolling_24h_page_limit
        self._global_max_processing_documents = global_max_processing_documents
        self._global_rolling_24h_page_limit = global_rolling_24h_page_limit
        self._memory_diagnostics_enabled = memory_diagnostics_enabled

    def create_upload(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        kind: DocumentKind,
        *,
        filename: str,
        content_type: str,
        size_bytes: int,
        upload_endpoint: str | None,
    ) -> tuple[Document, UploadTarget]:
        self._validate_upload(filename, content_type, size_bytes)
        with _quota_lock:
            self._lock_quota_guard()
            workspace = self._db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
            if workspace is None:
                self._db.rollback()
                raise DocumentProjectNotFoundError("工作区不存在")
            project = self._project(workspace_id, project_id)
            active_count = self._active_document_count(workspace_id)
            if active_count >= self._workspace_max_active_documents:
                self._db.rollback()
                raise DocumentQuotaError("当前已有一份资料正在排队或处理中，请完成后再上传。")
            document_id = uuid.uuid4()
            object_key = build_object_key(workspace_id, document_id)
            document = Document(
                id=document_id,
                project_id=project.id,
                kind=kind,
                original_name=filename,
                mime_type=content_type,
                size_bytes=size_bytes,
                storage_key=object_key,
                storage_backend=self._storage.backend_name,
                processing_status=DocumentProcessingStatus.UPLOADED,
                processing_phase="uploading",
                queue_priority=0 if workspace.role == WorkspaceRole.OWNER else 10,
            )
            self._db.add(document)
            self._db.commit()
        resolved_upload_endpoint = (
            upload_endpoint.format(document_id=document_id) if upload_endpoint is not None else None
        )
        target = self._storage.create_upload_url(
            object_key,
            workspace_id=workspace_id,
            document_id=document_id,
            content_type=content_type,
            expires_in=self._upload_url_expires_seconds,
            upload_endpoint=resolved_upload_endpoint,
        )
        self._db.refresh(document)
        return document, target

    def confirm_upload(self, workspace_id: uuid.UUID, project_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        document = self.get_document(workspace_id, project_id, document_id)
        if document.processing_status in {
            DocumentProcessingStatus.QUEUED,
            DocumentProcessingStatus.PROCESSING,
            DocumentProcessingStatus.INTERRUPTED,
            DocumentProcessingStatus.PARSED,
        }:
            return document
        if not document.storage_key:
            raise DocumentProcessingError("文档没有可恢复的对象存储文件")
        if document.storage_backend != self._storage.backend_name:
            raise DocumentProcessingError("当前存储配置与文档对象不一致，无法确认上传")
        validate_object_scope(document.storage_key, workspace_id, document.id)
        if not self._storage.object_exists(document.storage_key):
            raise DocumentProcessingError("上传文件不存在或上传尚未完成")
        actual_size = self._storage.object_size(document.storage_key)
        if actual_size > self._max_upload_bytes:
            self._reject_uploaded_object(
                document, f"PDF 文件不能超过 {self._max_upload_bytes // (1024 * 1024)}MB"
            )
        if actual_size != document.size_bytes:
            self._reject_uploaded_object(document, "上传文件大小与创建上传任务时不一致")
        with _quota_lock:
            self._lock_quota_guard()
            workspace = self._db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
            if workspace is None:
                self._db.rollback()
                raise DocumentProjectNotFoundError("工作区不存在")
            if self._active_document_count(workspace_id, exclude_document_id=document.id) >= self._workspace_max_active_documents:
                self._reject_uploaded_object(
                    document,
                    "当前已有一份资料正在排队或处理中，请完成后再上传。",
                    quota=True,
                )
            self._db.commit()
        local_path: Path | None = None
        try:
            local_path = self._storage.download_to_temp(document.storage_key)
            try:
                with fitz.open(local_path) as pdf:
                    total_pages = self._parser.validate_document(pdf)
            except PDFParsingError as exc:
                self._reject_uploaded_object(document, str(exc))
            except Exception as exc:
                self._reject_uploaded_object(document, "上传的文件不是有效 PDF")
                raise DocumentProcessingError("上传的文件不是有效 PDF") from exc
        finally:
            if local_path is not None:
                self._storage.release_temp(local_path)

        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=24)
        with _quota_lock:
            self._lock_quota_guard()
            workspace = self._db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
            if workspace is None:
                self._db.rollback()
                raise DocumentProjectNotFoundError("工作区不存在")
            if self._active_document_count(workspace_id, exclude_document_id=document.id) >= self._workspace_max_active_documents:
                self._reject_uploaded_object(document, "当前已有一份资料正在排队或处理中，请完成后再上传。", quota=True)
            is_owner = workspace.role == WorkspaceRole.OWNER
            if not is_owner:
                workspace_pages = self._rolling_page_total(cutoff, workspace_id=workspace_id)
                if workspace_pages + total_pages > self._workspace_rolling_24h_page_limit:
                    self._reject_uploaded_object(
                        document,
                        f"你最近24小时已处理{workspace_pages}页资料，接受本文件后将超过{self._workspace_rolling_24h_page_limit}页，请稍后再试。",
                        quota=True,
                    )
                global_pages = self._rolling_page_total(cutoff)
                if global_pages + total_pages > self._global_rolling_24h_page_limit:
                    self._reject_uploaded_object(
                        document,
                        "当前全站任务较多，最近24小时处理额度已用完，请稍后再试。",
                        quota=True,
                    )
            document.total_pages = total_pages
            document.quota_pages = total_pages
            document.accepted_at = now
            document.queued_at = now
            document.processing_status = DocumentProcessingStatus.QUEUED
            document.processing_phase = "queued"
            document.error_message = None
            self._db.commit()
        self._db.refresh(document)
        return document

    def get_document(self, workspace_id: uuid.UUID, project_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        document = self._db.scalar(
            select(Document)
            .join(Project, Document.project_id == Project.id)
            .where(
                Document.id == document_id,
                Document.project_id == project_id,
                Project.workspace_id == workspace_id,
            )
        )
        if document is None:
            raise DocumentNotFoundError("文档不存在")
        return document

    def cancel_document(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> Document:
        document = self._db.scalar(
            select(Document)
            .join(Project, Document.project_id == Project.id)
            .where(
                Document.id == document_id,
                Document.project_id == project_id,
                Project.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if document is None:
            raise DocumentNotFoundError("文档不存在")
        if document.processing_status == DocumentProcessingStatus.CANCELLED:
            self._delete_cancelled_object(document, workspace_id)
            return document
        if document.processing_status != DocumentProcessingStatus.INTERRUPTED:
            raise DocumentCancellationError("当前仅支持取消已暂停的文档任务")

        document.processing_status = DocumentProcessingStatus.CANCELLED
        document.processing_phase = "user_cancelled"
        document.cancelled_at = datetime.now(UTC)
        document.lease_owner = None
        document.lease_expires_at = None
        document.retry_not_before = None
        document.error_message = "用户已取消任务"
        self._db.commit()
        self._delete_cancelled_object(document, workspace_id)
        self._db.refresh(document)
        return document

    def latest_document(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, kind: DocumentKind
    ) -> Document | None:
        self._project(workspace_id, project_id)
        return self._db.scalar(
            select(Document)
            .where(Document.project_id == project_id, Document.kind == kind)
            .order_by(Document.created_at.desc())
            .limit(1)
        )

    def process_document(
        self,
        document_id: uuid.UUID,
        *,
        owner: str | None = None,
        interrupt_after_page: int | None = None,
        lease_acquired: bool = False,
    ) -> Document:
        lease_owner = owner or str(uuid.uuid4())
        if not lease_acquired and not self.acquire_lease(document_id, lease_owner):
            document = self._db.get(Document, document_id)
            if document is not None and document.processing_status == DocumentProcessingStatus.PARSED:
                return document
            raise LeaseUnavailableError("文档正在由其他进程处理")
        local_path: Path | None = None
        diagnostics = DocumentMemoryDiagnostics(
            self._db,
            enabled=self._memory_diagnostics_enabled,
        )
        try:
            diagnostics.start()
            document = self._db.get(Document, document_id)
            if document is None:
                raise DocumentNotFoundError("文档不存在")
            if not document.storage_key:
                raise StorageError("原始 PDF 已不存在，无法继续解析")
            if document.storage_backend != self._storage.backend_name:
                raise StorageError("当前存储配置与文档对象不一致，无法恢复解析")
            local_path = self._storage.download_to_temp(document.storage_key)
            pdf: fitz.Document | None = None
            try:
                pdf = fitz.open(local_path)
            except Exception as exc:
                raise PDFParsingError("上传的文件不是有效 PDF") from exc
            try:
                total_pages = self._parser.validate_document(pdf)
                retry_window_page = (
                    document.current_page
                    if document.processing_phase == "resource_limited"
                    else None
                )
                document.total_pages = total_pages
                document.processing_status = DocumentProcessingStatus.PROCESSING
                completed_numbers = set(
                    self._db.scalars(
                        select(DocumentPage.page_number).where(
                            DocumentPage.document_id == document.id,
                            DocumentPage.status == DocumentPageStatus.COMPLETED,
                        )
                    ).all()
                )
                first_pending = next(
                    (page for page in range(1, total_pages + 1) if page not in completed_numbers),
                    total_pages + 1,
                )
                document.processing_phase = "resuming" if completed_numbers and first_pending <= total_pages else "extracting"
                document.current_page = min(first_pending, total_pages)
                document.error_message = None
                document.retry_not_before = None
                self._db.commit()

                for page_number in range(1, total_pages + 1):
                    if page_number in completed_numbers:
                        continue
                    self._renew_lease(document.id, lease_owner)
                    document = self._db.get(Document, document.id) or document
                    document.current_page = page_number
                    if document.processing_phase == "resuming" and page_number > first_pending:
                        document.processing_phase = "extracting"
                    page_record = self._begin_page(document.id, page_number)
                    self._db.commit()
                    if pdf is None:
                        pdf = fitz.open(local_path)
                    page = pdf.load_page(page_number - 1)
                    allow_ocr = (
                        page_record.retry_count < _OCR_PAGE_RETRY_LIMIT
                        or page_number == retry_window_page
                    )
                    try:
                        extracted = self._parser.extract_text_page(page, page_number)
                        if extracted is None and self._parser.requires_page_for_ocr:
                            extracted = self._parser.extract_ocr_page(
                                page_number,
                                source_path=local_path,
                                allow_ocr=allow_ocr,
                                page=page,
                            )
                    except Exception as exc:
                        page_record.status = DocumentPageStatus.FAILED
                        page_record.error_message = self._safe_error(exc)
                        page_record.completed_at = datetime.now(UTC)
                        document.failed_pages = self._count_pages(document.id, DocumentPageStatus.FAILED)
                        self._db.commit()
                        raise
                    finally:
                        del page

                    if extracted is None:
                        pdf.close()
                        pdf = None
                        try:
                            extracted = self._parser.extract_ocr_page(
                                page_number,
                                source_path=local_path,
                                allow_ocr=allow_ocr,
                            )
                        except Exception as exc:
                            page_record.status = DocumentPageStatus.FAILED
                            page_record.error_message = self._safe_error(exc)
                            page_record.completed_at = datetime.now(UTC)
                            document.failed_pages = self._count_pages(document.id, DocumentPageStatus.FAILED)
                            self._db.commit()
                            raise

                    page_record.status = DocumentPageStatus.COMPLETED
                    page_record.extraction_method = extracted.extraction_method
                    page_record.extracted_text = extracted.text
                    page_record.character_count = len(extracted.text)
                    page_record.error_message = None
                    page_record.completed_at = datetime.now(UTC)
                    document.processed_pages = self._count_pages(document.id, DocumentPageStatus.COMPLETED)
                    document.failed_pages = self._count_pages(document.id, DocumentPageStatus.FAILED)
                    document.ocr_page_count = self._count_ocr_pages(document.id)
                    self._db.commit()
                    del extracted
                    diagnostics.page_completed(page_number, total_pages)
                    if interrupt_after_page == page_number:
                        raise SimulatedInterruption(f"模拟在第 {page_number} 页后中断")
            finally:
                if pdf is not None:
                    pdf.close()
            return self._finalize_document(document_id, lease_owner)
        except LeaseUnavailableError:
            raise
        except Exception as exc:
            self._mark_interrupted(document_id, lease_owner, exc)
            if isinstance(exc, (PDFParsingError, StorageError, SimulatedInterruption)):
                raise DocumentProcessingError(str(exc)) from exc
            raise DocumentProcessingError("PDF 逐页解析失败") from exc
        finally:
            diagnostics.close()
            self._parser.close()
            if local_path is not None:
                self._storage.release_temp(local_path)

    def acquire_lease(self, document_id: uuid.UUID, owner: str) -> bool:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self._lease_seconds)
        with _quota_lock:
            self._lock_quota_guard()
            processing_count = int(self._db.scalar(
                select(func.count(Document.id)).where(
                    Document.id != document_id,
                    Document.processing_status.in_([
                        DocumentProcessingStatus.PROCESSING,
                        DocumentProcessingStatus.PARSING,
                    ]),
                    Document.lease_expires_at >= now,
                )
            ) or 0)
            if processing_count >= self._global_max_processing_documents:
                self._db.rollback()
                return False
            statement = (
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.processing_status.in_([
                        DocumentProcessingStatus.QUEUED,
                        DocumentProcessingStatus.PROCESSING,
                        DocumentProcessingStatus.PARSING,
                        DocumentProcessingStatus.INTERRUPTED,
                    ]),
                    or_(Document.retry_not_before.is_(None), Document.retry_not_before <= now),
                    or_(Document.lease_owner.is_(None), Document.lease_expires_at < now, Document.lease_owner == owner),
                )
                .values(
                    lease_owner=owner,
                    lease_expires_at=expires,
                    processing_status=DocumentProcessingStatus.PROCESSING,
                    processing_started_at=func.coalesce(Document.processing_started_at, now),
                )
            )
            result = self._db.execute(statement)
            self._db.commit()
            return bool(result.rowcount)

    def claim_next(self, owner: str) -> uuid.UUID | None:
        """Atomically claim the oldest resumable document across all workspaces."""
        now = datetime.now(UTC)
        with _quota_lock:
            self._lock_quota_guard()
            active = int(self._db.scalar(
                select(func.count(Document.id)).where(
                    Document.processing_status.in_([
                        DocumentProcessingStatus.PROCESSING,
                        DocumentProcessingStatus.PARSING,
                    ]),
                    Document.lease_expires_at >= now,
                )
            ) or 0)
            if active >= self._global_max_processing_documents:
                self._db.rollback()
                return None
            candidate = self._db.scalar(
                select(Document)
                .where(
                    Document.processing_status.in_(QUEUE_DOCUMENT_STATUSES),
                    or_(Document.retry_not_before.is_(None), Document.retry_not_before <= now),
                    or_(Document.lease_owner.is_(None), Document.lease_expires_at < now),
                )
                .order_by(Document.queue_priority.asc(), Document.accepted_at.asc(), Document.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if candidate is None:
                self._db.rollback()
                return None
            candidate.lease_owner = owner
            candidate.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            candidate.processing_status = DocumentProcessingStatus.PROCESSING
            candidate.processing_started_at = candidate.processing_started_at or now
            if candidate.processed_pages:
                candidate.processing_phase = "resuming"
                candidate.current_page = min(candidate.total_pages, candidate.processed_pages + 1)
            self._db.commit()
            return candidate.id

    def resume_incomplete(self) -> list[uuid.UUID]:
        completed: list[uuid.UUID] = []
        while True:
            owner = str(uuid.uuid4())
            document_id = self.claim_next(owner)
            if document_id is None:
                break
            try:
                self.process_document(document_id, owner=owner, lease_acquired=True)
                completed.append(document_id)
            except LeaseUnavailableError:
                continue
            except DocumentProcessingError:
                continue
        return completed

    def queue_position(self, document: Document) -> int | None:
        if document.processing_status not in {DocumentProcessingStatus.QUEUED, DocumentProcessingStatus.INTERRUPTED}:
            return None
        ordered = list(self._db.scalars(
            select(Document.id)
            .where(Document.processing_status.in_([DocumentProcessingStatus.QUEUED, DocumentProcessingStatus.INTERRUPTED]))
            .order_by(Document.queue_priority.asc(), Document.accepted_at.asc(), Document.created_at.asc())
        ).all())
        try:
            return ordered.index(document.id) + 1
        except ValueError:
            return None

    async def process_upload(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        kind: DocumentKind,
        file: UploadFile,
    ) -> Document:
        """Compatibility path for local API/tests; production uses direct object upload."""
        if not isinstance(self._storage, LocalStorageProvider):
            raise DocumentProcessingError("生产环境必须使用对象存储直传")
        filename = file.filename or "document.pdf"
        content_type = file.content_type or ""
        document, _ = self.create_upload(
            workspace_id,
            project_id,
            kind,
            filename=filename,
            content_type=content_type,
            size_bytes=self._content_length(file),
            upload_endpoint="http://local.invalid/upload",
        )
        assert document.storage_key is not None
        try:
            _, actual_size = await self._storage.save_pdf(document.storage_key, file)
            document.size_bytes = actual_size
            self._db.commit()
            self.confirm_upload(workspace_id, project_id, document.id)
            return self.process_document(document.id)
        except UploadLimitError as exc:
            self._db.rollback()
            stored = self._db.get(Document, document.id)
            if stored is not None:
                stored.processing_status = DocumentProcessingStatus.FAILED
                stored.processing_phase = "failed"
                stored.error_message = str(exc)
                self._db.commit()
            raise DocumentProcessingError(str(exc)) from exc

    def _finalize_document(self, document_id: uuid.UUID, owner: str) -> Document:
        document = self._db.get(Document, document_id)
        if document is None:
            raise DocumentNotFoundError("文档不存在")
        pages = list(
            self._db.scalars(
                select(DocumentPage)
                .where(
                    DocumentPage.document_id == document.id,
                    DocumentPage.status == DocumentPageStatus.COMPLETED,
                )
                .order_by(DocumentPage.page_number)
            ).all()
        )
        if len(pages) != document.total_pages:
            raise DocumentProcessingError("仍有页面未成功提取，暂不能整理内容")
        document.processing_phase = "structuring"
        self._db.commit()
        parsed_pdf = ParsedPDF(
            page_count=document.total_pages,
            pages=[ParsedPageData(page.page_number, page.extracted_text or "") for page in pages],
            parser_name="pymupdf+rapidocr" if document.ocr_page_count else "pymupdf",
            parser_version=fitz.VersionBind,
            is_scanned=document.ocr_page_count == document.total_pages and document.total_pages > 0,
            ocr_executed=document.ocr_page_count > 0,
            ocr_page_count=document.ocr_page_count,
            ocr_error=None,
        )
        structured = self._structurer.structure(parsed_pdf)
        chunks = self._splitter.split(structured)
        existing = self._db.scalar(select(ParsedDocument).where(ParsedDocument.document_id == document.id))
        if existing is not None:
            self._db.delete(existing)
            self._db.flush()
        document.parsed_document = ParsedDocument(
            document_id=document.id,
            page_count=parsed_pdf.page_count,
            raw_text=parsed_pdf.raw_text,
            parser_name=parsed_pdf.parser_name,
            parser_version=parsed_pdf.parser_version,
            is_scanned=parsed_pdf.is_scanned,
            ocr_executed=parsed_pdf.ocr_executed,
            ocr_page_count=parsed_pdf.ocr_page_count,
            ocr_error=parsed_pdf.ocr_error,
            pages=[ParsedPage(page_number=page.page_number, text=page.text) for page in parsed_pdf.pages],
            chunks=[
                TextChunk(
                    position=chunk.position,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    chapter_title=chunk.chapter_title,
                    section_title=chunk.section_title,
                    content=chunk.content,
                )
                for chunk in chunks
            ],
        )
        document.processing_status = DocumentProcessingStatus.PARSED
        document.processing_phase = "completed"
        document.processed_pages = document.total_pages
        document.failed_pages = 0
        document.current_page = document.total_pages
        document.error_message = None
        document.lease_owner = None
        document.lease_expires_at = None
        self._db.commit()
        self._db.refresh(document)
        if document.storage_key:
            object_key = document.storage_key
            try:
                self._storage.delete_object(object_key)
                document.storage_key = None
                self._db.commit()
            except Exception:
                # The cleanup endpoint retries deletion; parsed content remains usable.
                self._db.rollback()
        return document

    def _begin_page(self, document_id: uuid.UUID, page_number: int) -> DocumentPage:
        page = self._db.scalar(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id,
                DocumentPage.page_number == page_number,
            )
        )
        if page is None:
            page = DocumentPage(document_id=document_id, page_number=page_number)
            self._db.add(page)
        else:
            page.retry_count = int(page.retry_count or 0) + 1
        page.status = DocumentPageStatus.PROCESSING
        page.started_at = datetime.now(UTC)
        page.completed_at = None
        page.error_message = None
        return page

    def _renew_lease(self, document_id: uuid.UUID, owner: str) -> None:
        result = self._db.execute(
            update(Document)
            .where(Document.id == document_id, Document.lease_owner == owner)
            .values(lease_expires_at=datetime.now(UTC) + timedelta(seconds=self._lease_seconds))
        )
        if not result.rowcount:
            self._db.rollback()
            raise LeaseUnavailableError("文档解析租约已丢失")
        self._db.commit()

    def _mark_interrupted(self, document_id: uuid.UUID, owner: str, exc: Exception) -> None:
        self._db.rollback()
        document = self._db.get(Document, document_id)
        if document is None or document.lease_owner != owner:
            return
        document.retry_count = int(document.retry_count or 0) + 1
        resource_limited = isinstance(exc, OCRResourceLimitedError)
        ocr_disabled = isinstance(exc, OCRDisabledError)
        missing_source = isinstance(exc, StorageError) and "不存在" in str(exc)
        invalid_pdf = isinstance(exc, PDFParsingError) and (
            "页数不能超过" in str(exc) or "不是有效 PDF" in str(exc) or "无法打开 PDF" in str(exc)
        )
        exhausted = (
            document.retry_count >= self._max_document_retries
            and not isinstance(exc, SimulatedInterruption)
            and not resource_limited
        )
        document.processing_status = (
            DocumentProcessingStatus.FAILED
            if missing_source or invalid_pdf or exhausted
            else DocumentProcessingStatus.INTERRUPTED
        )
        document.processing_phase = (
            "failed"
            if document.processing_status == DocumentProcessingStatus.FAILED
            else "resource_limited"
            if resource_limited
            else "interrupted"
        )
        if document.processing_status == DocumentProcessingStatus.INTERRUPTED and document.total_pages:
            document.current_page = min(document.total_pages, document.processed_pages + 1)
        if resource_limited:
            page_attempts = int(self._db.scalar(
                select(DocumentPage.retry_count).where(
                    DocumentPage.document_id == document.id,
                    DocumentPage.page_number == document.current_page,
                )
            ) or 0)
            delay_seconds = (
                _OCR_RESOURCE_PAUSE_SECONDS
                if page_attempts >= _OCR_PAGE_RETRY_LIMIT - 1
                else min(
                    _OCR_RESOURCE_PAUSE_SECONDS,
                    _OCR_RETRY_BASE_SECONDS * (2 ** max(0, page_attempts)),
                )
            )
            document.retry_not_before = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            document.error_message = _OCR_DISABLED_REASON if ocr_disabled else _OCR_RESOURCE_MESSAGE
        else:
            document.retry_not_before = None
            document.error_message = self._safe_error(exc)
        document.lease_owner = None
        document.lease_expires_at = None
        if document.processing_status == DocumentProcessingStatus.FAILED:
            self._release_quota_once(document)
        self._db.commit()

    def _count_pages(self, document_id: uuid.UUID, status: DocumentPageStatus) -> int:
        return int(self._db.scalar(
            select(func.count(DocumentPage.id)).where(
                DocumentPage.document_id == document_id,
                DocumentPage.status == status,
            )
        ) or 0)

    def _count_ocr_pages(self, document_id: uuid.UUID) -> int:
        return int(self._db.scalar(
            select(func.count(DocumentPage.id)).where(
                DocumentPage.document_id == document_id,
                DocumentPage.status == DocumentPageStatus.COMPLETED,
                DocumentPage.extraction_method == ExtractionMethod.OCR,
            )
        ) or 0)

    def _lock_quota_guard(self) -> QuotaGuard:
        guard = self._db.scalar(select(QuotaGuard).where(QuotaGuard.id == 1).with_for_update())
        if guard is None:
            guard = QuotaGuard(id=1)
            self._db.add(guard)
            self._db.flush()
        return guard

    def _active_document_count(
        self,
        workspace_id: uuid.UUID,
        *,
        exclude_document_id: uuid.UUID | None = None,
    ) -> int:
        query = (
            select(func.count(Document.id))
            .join(Project, Document.project_id == Project.id)
            .where(Project.workspace_id == workspace_id, Document.processing_status.in_(ACTIVE_DOCUMENT_STATUSES))
        )
        if exclude_document_id is not None:
            query = query.where(Document.id != exclude_document_id)
        return int(self._db.scalar(query) or 0)

    def _rolling_page_total(self, cutoff: datetime, workspace_id: uuid.UUID | None = None) -> int:
        query = (
            select(func.coalesce(func.sum(Document.quota_pages), 0))
            .join(Project, Document.project_id == Project.id)
            .join(Workspace, Project.workspace_id == Workspace.id)
            .where(
                Document.accepted_at >= cutoff,
                Document.quota_released_at.is_(None),
                Workspace.role == WorkspaceRole.PUBLIC,
            )
        )
        if workspace_id is not None:
            query = query.where(Project.workspace_id == workspace_id)
        return int(self._db.scalar(query) or 0)

    def _reject_uploaded_object(self, document: Document, message: str, *, quota: bool = False) -> None:
        if document.storage_key:
            self._storage.delete_object(document.storage_key)
            document.storage_key = None
        document.processing_status = DocumentProcessingStatus.FAILED
        document.processing_phase = "failed"
        document.error_message = message
        document.quota_pages = 0
        document.accepted_at = None
        document.queued_at = None
        self._db.commit()
        if quota:
            raise DocumentQuotaError(message)
        raise DocumentProcessingError(message)

    def _delete_cancelled_object(self, document: Document, workspace_id: uuid.UUID) -> None:
        if not document.storage_key:
            return
        object_key = document.storage_key
        try:
            validate_object_scope(object_key, workspace_id, document.id)
            self._storage.delete_object(object_key)
        except Exception:
            # Cancellation is already durable; object cleanup is best-effort.
            return
        document.storage_key = None
        self._db.commit()

    @staticmethod
    def _release_quota_once(document: Document) -> bool:
        if document.processed_pages != 0 or document.quota_pages <= 0 or document.quota_released_at is not None:
            return False
        document.quota_released_at = datetime.now(UTC)
        return True

    def _project(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        project = self._db.scalar(
            select(Project).where(Project.id == project_id, Project.workspace_id == workspace_id)
        )
        if project is None:
            raise DocumentProjectNotFoundError(f"Project {project_id} was not found")
        return project

    def _validate_upload(self, filename: str, content_type: str, size_bytes: int) -> None:
        if not filename.lower().endswith(".pdf"):
            raise DocumentProcessingError("仅支持扩展名为 .pdf 的文件")
        if content_type.casefold() != "application/pdf":
            raise DocumentProcessingError("文件 MIME 类型必须为 application/pdf")
        if size_bytes <= 0:
            raise DocumentProcessingError("PDF 文件为空")
        if size_bytes > self._max_upload_bytes:
            raise DocumentProcessingError(
                f"PDF 文件不能超过 {self._max_upload_bytes // (1024 * 1024)}MB"
            )

    @staticmethod
    def _content_length(file: UploadFile) -> int:
        position = file.file.tell()
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(position)
        return size

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return (str(exc).strip() or exc.__class__.__name__)[:1000]


class DocumentTaskRunner:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        service_factory: Callable[[Session], DocumentProcessingService],
    ) -> None:
        self._session_factory = session_factory
        self._service_factory = service_factory

    def run(self, document_id: uuid.UUID | None = None) -> None:
        """Compatibility entry point; the queue, not a caller-supplied id, decides order."""
        self.resume_incomplete()

    def resume_incomplete(self) -> list[uuid.UUID]:
        with self._session_factory() as db:
            return self._service_factory(db).resume_incomplete()
