from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

import fitz
from fastapi import UploadFile
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.document.parser import PDFParser, PDFParsingError, ParsedPDF, ParsedPageData
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.models.document import DocumentPage, ParsedDocument, ParsedPage, TextChunk
from app.models.enums import (
    DocumentKind,
    DocumentPageStatus,
    DocumentProcessingStatus,
    ExtractionMethod,
)
from app.models.project import Document, Project
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


class DocumentProjectNotFoundError(LookupError):
    pass


class DocumentNotFoundError(LookupError):
    pass


class LeaseUnavailableError(RuntimeError):
    pass


class SimulatedInterruption(RuntimeError):
    pass


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
        project = self._project(workspace_id, project_id)
        self._validate_upload(filename, content_type, size_bytes)
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
            self._storage.delete_object(document.storage_key)
            document.processing_status = DocumentProcessingStatus.FAILED
            document.processing_phase = "failed"
            document.error_message = f"PDF 文件不能超过 {self._max_upload_bytes // (1024 * 1024)}MB"
            self._db.commit()
            raise DocumentProcessingError(document.error_message)
        if actual_size != document.size_bytes:
            raise DocumentProcessingError("上传文件大小与创建上传任务时不一致")
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
    ) -> Document:
        lease_owner = owner or str(uuid.uuid4())
        if not self.acquire_lease(document_id, lease_owner):
            document = self._db.get(Document, document_id)
            if document is not None and document.processing_status == DocumentProcessingStatus.PARSED:
                return document
            raise LeaseUnavailableError("文档正在由其他进程处理")
        local_path: Path | None = None
        try:
            document = self._db.get(Document, document_id)
            if document is None:
                raise DocumentNotFoundError("文档不存在")
            if not document.storage_key:
                raise StorageError("原始 PDF 已不存在，无法继续解析")
            if document.storage_backend != self._storage.backend_name:
                raise StorageError("当前存储配置与文档对象不一致，无法恢复解析")
            local_path = self._storage.download_to_temp(document.storage_key)
            try:
                pdf_document = fitz.open(local_path)
            except Exception as exc:
                raise PDFParsingError("上传的文件不是有效 PDF") from exc
            with pdf_document as pdf:
                total_pages = self._parser.validate_document(pdf)
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
                    page = pdf.load_page(page_number - 1)
                    try:
                        extracted = self._parser.extract_page(page, page_number)
                    except Exception as exc:
                        page_record.status = DocumentPageStatus.FAILED
                        page_record.error_message = self._safe_error(exc)
                        page_record.completed_at = datetime.now(UTC)
                        document.failed_pages = self._count_pages(document.id, DocumentPageStatus.FAILED)
                        self._db.commit()
                        raise
                    finally:
                        page = None

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
                    if interrupt_after_page == page_number:
                        raise SimulatedInterruption(f"模拟在第 {page_number} 页后中断")

            return self._finalize_document(document_id, lease_owner)
        except LeaseUnavailableError:
            raise
        except Exception as exc:
            self._mark_interrupted(document_id, lease_owner, exc)
            if isinstance(exc, (PDFParsingError, StorageError, SimulatedInterruption)):
                raise DocumentProcessingError(str(exc)) from exc
            raise DocumentProcessingError("PDF 逐页解析失败") from exc
        finally:
            if local_path is not None:
                self._storage.release_temp(local_path)

    def acquire_lease(self, document_id: uuid.UUID, owner: str) -> bool:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self._lease_seconds)
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
                or_(Document.lease_owner.is_(None), Document.lease_expires_at < now, Document.lease_owner == owner),
            )
            .values(
                lease_owner=owner,
                lease_expires_at=expires,
                processing_status=DocumentProcessingStatus.PROCESSING,
            )
        )
        result = self._db.execute(statement)
        self._db.commit()
        return bool(result.rowcount)

    def resume_incomplete(self) -> list[uuid.UUID]:
        document_ids = list(
            self._db.scalars(
                select(Document.id).where(
                    Document.processing_status.in_([
                        DocumentProcessingStatus.QUEUED,
                        DocumentProcessingStatus.PROCESSING,
                        DocumentProcessingStatus.PARSING,
                        DocumentProcessingStatus.INTERRUPTED,
                    ])
                )
            ).all()
        )
        completed: list[uuid.UUID] = []
        for document_id in document_ids:
            try:
                self.process_document(document_id)
                completed.append(document_id)
            except LeaseUnavailableError:
                continue
            except DocumentProcessingError:
                continue
        return completed

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
        missing_source = isinstance(exc, StorageError) and "不存在" in str(exc)
        invalid_pdf = isinstance(exc, PDFParsingError) and (
            "页数不能超过" in str(exc) or "不是有效 PDF" in str(exc) or "无法打开 PDF" in str(exc)
        )
        exhausted = document.retry_count >= self._max_document_retries and not isinstance(exc, SimulatedInterruption)
        document.processing_status = (
            DocumentProcessingStatus.FAILED
            if missing_source or invalid_pdf or exhausted
            else DocumentProcessingStatus.INTERRUPTED
        )
        document.processing_phase = "failed" if document.processing_status == DocumentProcessingStatus.FAILED else "interrupted"
        if document.processing_status == DocumentProcessingStatus.INTERRUPTED and document.total_pages:
            document.current_page = min(document.total_pages, document.processed_pages + 1)
        document.error_message = self._safe_error(exc)
        document.lease_owner = None
        document.lease_expires_at = None
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

    def run(self, document_id: uuid.UUID) -> None:
        with self._session_factory() as db:
            try:
                self._service_factory(db).process_document(document_id)
            except (DocumentProcessingError, LeaseUnavailableError):
                return

    def resume_incomplete(self) -> list[uuid.UUID]:
        with self._session_factory() as db:
            return self._service_factory(db).resume_incomplete()
