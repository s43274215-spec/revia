from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.document.parser import SourceOutlineEntry
from app.models.document import DocumentPage
from app.models.enums import (
    DocumentPageStatus,
    DocumentProcessingStatus,
    ExtractionMethod,
    ProjectStatus,
)
from app.models.project import Document, Project
from app.schemas.github_ocr import GitHubOCRClaimRead, GitHubOCRProgressRead
from app.services.document_processing import DocumentProcessingService
from app.services.storage import StorageProvider


class GitHubOCRJobError(RuntimeError):
    pass


class GitHubOCRJobNotFoundError(GitHubOCRJobError):
    pass


class GitHubOCRJobConflictError(GitHubOCRJobError):
    pass


class GitHubOCRJobService:
    def __init__(
        self,
        *,
        db: Session,
        storage: StorageProvider,
        processing: DocumentProcessingService,
        lease_seconds: int,
        download_url_expires_seconds: int,
        ocr_dpi: int,
        minimum_text_length: int,
        max_pdf_pages: int,
    ) -> None:
        self._db = db
        self._storage = storage
        self._processing = processing
        self._lease_seconds = lease_seconds
        self._download_url_expires_seconds = download_url_expires_seconds
        self._ocr_dpi = ocr_dpi
        self._minimum_text_length = minimum_text_length
        self._max_pdf_pages = max_pdf_pages

    def claim(self, document_id: uuid.UUID, attempt_id: uuid.UUID) -> GitHubOCRClaimRead:
        document = self._locked_document(document_id)
        self._require_attempt(document, attempt_id)
        if not document.storage_key:
            raise GitHubOCRJobConflictError("原始 PDF 已不存在")
        if document.storage_backend != self._storage.backend_name:
            raise GitHubOCRJobConflictError("当前存储配置与文档对象不一致")

        document.processing_phase = "external_ocr_processing"
        document.lease_expires_at = self._lease_expiry()
        first_pending = self._first_pending_page(document)
        if first_pending is not None:
            document.current_page = first_pending
        self._db.commit()

        download_url = self._storage.create_download_url(
            document.storage_key,
            expires_in=self._download_url_expires_seconds,
        )
        completed_pages = list(self._db.scalars(
            select(DocumentPage.page_number)
            .where(
                DocumentPage.document_id == document.id,
                DocumentPage.status == DocumentPageStatus.COMPLETED,
            )
            .order_by(DocumentPage.page_number)
        ).all())
        return GitHubOCRClaimRead(
            document_id=document.id,
            attempt_id=attempt_id,
            original_name=document.original_name,
            download_url=download_url,
            size_bytes=document.size_bytes,
            total_pages=document.total_pages,
            completed_pages=completed_pages,
            ocr_dpi=self._ocr_dpi,
            minimum_text_length=self._minimum_text_length,
            max_pdf_pages=self._max_pdf_pages,
            heartbeat_seconds=max(15, min(60, self._lease_seconds // 3)),
        )

    def heartbeat(
        self,
        document_id: uuid.UUID,
        attempt_id: uuid.UUID,
        *,
        current_page: int | None = None,
    ) -> GitHubOCRProgressRead:
        document = self._locked_document(document_id)
        self._require_attempt(document, attempt_id)
        if current_page is not None and document.total_pages:
            document.current_page = min(max(1, current_page), document.total_pages)
        document.processing_phase = "external_ocr_processing"
        document.lease_expires_at = self._lease_expiry()
        self._db.commit()
        return self._progress(document)

    def complete_page(
        self,
        document_id: uuid.UUID,
        attempt_id: uuid.UUID,
        page_number: int,
        *,
        text: str,
        extraction_method: ExtractionMethod,
    ) -> GitHubOCRProgressRead:
        document = self._locked_document(document_id)
        self._require_attempt(document, attempt_id)
        if not 1 <= page_number <= document.total_pages:
            raise GitHubOCRJobConflictError("页码超出文档范围")

        page = self._db.scalar(
            select(DocumentPage)
            .where(
                DocumentPage.document_id == document.id,
                DocumentPage.page_number == page_number,
            )
            .with_for_update()
        )
        if page is None:
            page = DocumentPage(document_id=document.id, page_number=page_number)
            self._db.add(page)
        if page.status != DocumentPageStatus.COMPLETED:
            page.status = DocumentPageStatus.COMPLETED
            page.extraction_method = extraction_method
            page.extracted_text = self._normalize_text(text)
            page.character_count = len(page.extracted_text)
            page.error_message = None
            page.started_at = page.started_at or datetime.now(UTC)
            page.completed_at = datetime.now(UTC)
        self._db.flush()

        document.processed_pages = self._count_pages(document.id, DocumentPageStatus.COMPLETED)
        document.failed_pages = self._count_pages(document.id, DocumentPageStatus.FAILED)
        document.ocr_page_count = self._count_ocr_pages(document.id)
        document.processing_status = DocumentProcessingStatus.PROCESSING
        document.processing_phase = "external_ocr_processing"
        next_page = self._first_pending_page(document)
        document.current_page = next_page if next_page is not None else document.total_pages
        document.error_message = None
        document.lease_expires_at = self._lease_expiry()
        self._db.commit()
        return self._progress(document)

    def finish(
        self,
        document_id: uuid.UUID,
        attempt_id: uuid.UUID,
        *,
        outline: tuple[SourceOutlineEntry, ...] = (),
    ) -> Document:
        document = self._locked_document(document_id)
        if document.processing_status == DocumentProcessingStatus.PARSED:
            return document
        self._require_attempt(document, attempt_id)
        completed = self._count_pages(document.id, DocumentPageStatus.COMPLETED)
        if completed != document.total_pages:
            raise GitHubOCRJobConflictError(
                f"仍有 {document.total_pages - completed} 页未回传，不能完成任务"
            )
        document.processing_phase = "structuring"
        document.lease_expires_at = self._lease_expiry()
        self._db.commit()
        return self._processing.finalize_external_document(
            document.id,
            owner=self._owner(attempt_id),
            source_outline=outline,
        )

    def fail(
        self,
        document_id: uuid.UUID,
        attempt_id: uuid.UUID,
        *,
        error_message: str,
        page_number: int | None = None,
    ) -> Document:
        document = self._locked_document(document_id)
        if document.processing_status in {
            DocumentProcessingStatus.FAILED,
            DocumentProcessingStatus.CANCELLED,
            DocumentProcessingStatus.PARSED,
        }:
            return document
        self._require_attempt(document, attempt_id)
        if page_number is not None and 1 <= page_number <= document.total_pages:
            page = self._db.scalar(
                select(DocumentPage)
                .where(
                    DocumentPage.document_id == document.id,
                    DocumentPage.page_number == page_number,
                )
                .with_for_update()
            )
            if page is None:
                page = DocumentPage(document_id=document.id, page_number=page_number)
                self._db.add(page)
            if page.status != DocumentPageStatus.COMPLETED:
                page.status = DocumentPageStatus.FAILED
                page.error_message = self._safe_error(error_message)
                page.completed_at = datetime.now(UTC)
            document.current_page = page_number
        document.failed_pages = self._count_pages(document.id, DocumentPageStatus.FAILED)
        document.processing_status = DocumentProcessingStatus.FAILED
        document.processing_phase = "failed"
        document.error_message = self._safe_error(error_message)
        document.retry_not_before = None
        document.lease_owner = None
        document.lease_expires_at = None
        project = self._db.get(Project, document.project_id)
        if project is not None and not project.chapters:
            project.status = ProjectStatus.FAILED
        self._processing.release_quota_if_unused(document)
        self._db.commit()
        self._db.refresh(document)
        return document

    def _locked_document(self, document_id: uuid.UUID) -> Document:
        document = self._db.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        if document is None:
            raise GitHubOCRJobNotFoundError("OCR 文档任务不存在")
        return document

    def _require_attempt(self, document: Document, attempt_id: uuid.UUID) -> None:
        if document.processing_status == DocumentProcessingStatus.CANCELLED:
            raise GitHubOCRJobConflictError("OCR 任务已取消")
        if document.processing_status == DocumentProcessingStatus.PARSED:
            raise GitHubOCRJobConflictError("OCR 任务已经完成")
        if document.processing_status != DocumentProcessingStatus.PROCESSING:
            raise GitHubOCRJobConflictError("OCR 任务当前不可执行")
        if document.lease_owner != self._owner(attempt_id):
            raise GitHubOCRJobConflictError("OCR 尝试已失效或已被新的任务替代")
        if document.processing_phase not in {
            "external_ocr_queued",
            "external_ocr_processing",
            "structuring",
        }:
            raise GitHubOCRJobConflictError("OCR 任务阶段不匹配")

    def _first_pending_page(self, document: Document) -> int | None:
        completed = set(self._db.scalars(
            select(DocumentPage.page_number).where(
                DocumentPage.document_id == document.id,
                DocumentPage.status == DocumentPageStatus.COMPLETED,
            )
        ).all())
        return next((number for number in range(1, document.total_pages + 1) if number not in completed), None)

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

    def _lease_expiry(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self._lease_seconds)

    @staticmethod
    def _owner(attempt_id: uuid.UUID) -> str:
        return f"github:{attempt_id}"

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    @staticmethod
    def _safe_error(message: str) -> str:
        normalized = " ".join(str(message).split())
        return (normalized or "GitHub OCR 任务失败")[:1000]

    @staticmethod
    def _progress(document: Document) -> GitHubOCRProgressRead:
        return GitHubOCRProgressRead(
            document_id=document.id,
            processing_status=document.processing_status.value,
            processing_phase=document.processing_phase,
            processed_pages=document.processed_pages,
            total_pages=document.total_pages,
            current_page=document.current_page,
        )
