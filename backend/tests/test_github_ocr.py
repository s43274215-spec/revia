from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import fitz
import httpx
from pydantic import ValidationError
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.documents import build_document_processing_service
from app.api.v1.endpoints.internal_ocr import require_github_ocr_worker
from app.core.config import Settings
from app.db.base import Base
from app.document.github_ocr import GitHubOCRDispatchError, GitHubOCRDispatcher
from app.document.parser import PDFParser, SourceOutlineEntry
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.models.document import DocumentPage, ParsedDocument
from app.models.enums import DocumentKind, DocumentPageStatus, DocumentProcessingStatus, ExtractionMethod
from app.models.project import Document, Project
from app.models.workspace import Workspace
from app.services.document_processing import DocumentProcessingService
from app.services.github_ocr import GitHubOCRJobConflictError, GitHubOCRJobService
from app.services.storage import LocalStorageProvider, build_object_key


def build_text_pdf() -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Editable PDF page with enough searchable text for local extraction.")
    data = pdf.tobytes()
    pdf.close()
    return data


def build_mixed_pdf() -> bytes:
    image_source = fitz.open()
    image_page = image_source.new_page(width=595, height=842)
    image_page.insert_text((72, 120), "SCANNED SECOND PAGE", fontsize=24)
    image = image_page.get_pixmap(dpi=120, colorspace=fitz.csGRAY, alpha=False).tobytes("png")
    image_source.close()
    pdf = fitz.open()
    first = pdf.new_page(width=595, height=842)
    first.insert_text((72, 72), "Editable first page with enough searchable text for local extraction.")
    second = pdf.new_page(width=595, height=842)
    second.insert_image(second.rect, stream=image)
    data = pdf.tobytes()
    pdf.close()
    return data


def build_scanned_pdf(page_count: int = 1) -> bytes:
    source = fitz.open()
    image_page = source.new_page(width=595, height=842)
    image_page.insert_text((72, 120), "SCANNED PAGE", fontsize=24)
    pix = image_page.get_pixmap(dpi=120, colorspace=fitz.csGRAY, alpha=False)
    image = pix.tobytes("png")
    source.close()
    pdf = fitz.open()
    for _ in range(page_count):
        page = pdf.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=image)
    data = pdf.tobytes()
    pdf.close()
    return data


class RecordingDispatcher:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.error = error

    def dispatch(self, document_id: uuid.UUID, attempt_id: uuid.UUID):
        self.calls.append((document_id, attempt_id))
        if self.error is not None:
            raise self.error
        return None


class GitHubOCRDispatcherTests(unittest.TestCase):
    def test_dispatch_uses_only_document_and_attempt_inputs(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"workflow_run_id": 123, "html_url": "https://example/run/123"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        dispatcher = GitHubOCRDispatcher(
            token="token",
            repository="owner/repo",
            workflow="revia-ocr.yml",
            ref="main",
            client=client,
        )
        document_id, attempt_id = uuid.uuid4(), uuid.uuid4()
        result = dispatcher.dispatch(document_id, attempt_id)
        self.assertEqual(result.workflow_run_id, 123)
        payload = __import__("json").loads(seen[0].content)
        self.assertEqual(payload["ref"], "main")
        self.assertEqual(payload["inputs"], {"document_id": str(document_id), "attempt_id": str(attempt_id)})
        self.assertNotIn("api_url", payload["inputs"])
        self.assertEqual(seen[0].headers["authorization"], "Bearer token")
        client.close()


class GitHubOCRSettingsTests(unittest.TestCase):
    def test_partial_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, github_ocr_token="token")

    def test_complete_configuration_enables_dispatcher(self) -> None:
        settings = Settings(
            _env_file=None,
            github_ocr_token="token",
            github_ocr_repository="owner/repo",
            github_ocr_worker_key="worker-secret",
        )
        self.assertTrue(settings.github_ocr_enabled)
        service = build_document_processing_service(Mock(), settings)
        self.assertIsNotNone(service._external_ocr_dispatcher)


class GitHubOCRWorkerAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            _env_file=None,
            github_ocr_token="token",
            github_ocr_repository="owner/repo",
            github_ocr_worker_key="worker-secret",
        )

    def test_worker_key_is_required_and_compared(self) -> None:
        require_github_ocr_worker(self.settings, "Bearer worker-secret")
        with self.assertRaises(HTTPException) as raised:
            require_github_ocr_worker(self.settings, "Bearer wrong")
        self.assertEqual(raised.exception.status_code, 401)

    def test_disabled_worker_endpoint_returns_service_unavailable(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            require_github_ocr_worker(Settings(_env_file=None), None)
        self.assertEqual(raised.exception.status_code, 503)

    def test_workflow_uses_fixed_secret_api_url_and_per_document_concurrency(self) -> None:
        workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "revia-ocr.yml"
        content = workflow.read_text(encoding="utf-8")
        self.assertIn("REVIA_API_BASE_URL: ${{ secrets.REVIA_API_BASE_URL }}", content)
        self.assertIn("group: revia-ocr-${{ inputs.document_id }}", content)
        self.assertNotIn("api_base_url:", content)


class GitHubOCRFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.workspace_id, self.project_id = uuid.uuid4(), uuid.uuid4()
        self.storage = LocalStorageProvider(Path(self.temp.name), max_upload_bytes=20 * 1024 * 1024)
        with self.Session() as db:
            db.add(Workspace(id=self.workspace_id))
            db.add(Project(id=self.project_id, workspace_id=self.workspace_id, name="GitHub OCR"))
            db.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def add_document(self, content: bytes, *, status=DocumentProcessingStatus.QUEUED) -> uuid.UUID:
        document_id = uuid.uuid4()
        key = build_object_key(self.workspace_id, document_id)
        path = self.storage.resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        with self.Session() as db:
            db.add(Document(
                id=document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="document.pdf",
                mime_type="application/pdf",
                size_bytes=len(content),
                storage_key=key,
                storage_backend="local",
                processing_status=status,
                processing_phase="queued" if status == DocumentProcessingStatus.QUEUED else "external_ocr_queued",
                total_pages=fitz.open(stream=content, filetype="pdf").page_count,
            ))
            db.commit()
        return document_id

    def service(self, db, dispatcher=None) -> DocumentProcessingService:
        return DocumentProcessingService(
            db,
            self.storage,
            PDFParser(max_pages=600),
            TextStructurer(),
            StructuredTextSplitter(),
            lease_seconds=30,
            external_ocr_dispatcher=dispatcher,
            external_ocr_lease_seconds=300,
        )

    def test_editable_pdf_stays_local_and_never_dispatches(self) -> None:
        document_id = self.add_document(build_text_pdf())
        dispatcher = RecordingDispatcher()
        with self.Session() as db:
            completed = self.service(db, dispatcher).process_document(document_id)
            self.assertEqual(completed.processing_status, DocumentProcessingStatus.PARSED)
            self.assertEqual(completed.ocr_page_count, 0)
            self.assertEqual(dispatcher.calls, [])

    def test_mixed_pdf_keeps_text_pages_local_and_dispatches_only_at_scan_page(self) -> None:
        document_id = self.add_document(build_mixed_pdf())
        dispatcher = RecordingDispatcher()
        with self.Session() as db:
            waiting = self.service(db, dispatcher).process_document(document_id)
            self.assertEqual(waiting.processing_phase, "external_ocr_queued")
            self.assertEqual(waiting.current_page, 2)
            pages = list(db.scalars(
                select(DocumentPage).where(DocumentPage.document_id == document_id).order_by(DocumentPage.page_number)
            ).all())
            self.assertEqual([page.status for page in pages], [DocumentPageStatus.COMPLETED, DocumentPageStatus.PENDING])
            self.assertEqual(pages[0].extraction_method, ExtractionMethod.TEXT)
            self.assertEqual(waiting.processed_pages, 1)

    def test_first_scanned_page_dispatches_automatically(self) -> None:
        document_id = self.add_document(build_scanned_pdf(2))
        dispatcher = RecordingDispatcher()
        with self.Session() as db:
            waiting = self.service(db, dispatcher).process_document(document_id)
            self.assertEqual(waiting.processing_status, DocumentProcessingStatus.PROCESSING)
            self.assertEqual(waiting.processing_phase, "external_ocr_queued")
            self.assertEqual(waiting.current_page, 1)
            self.assertEqual(len(dispatcher.calls), 1)
            page = db.scalar(select(DocumentPage).where(DocumentPage.document_id == document_id))
            self.assertIsNotNone(page)
            self.assertEqual(page.status, DocumentPageStatus.PENDING)
            self.assertIsNotNone(waiting.storage_key)

    def test_expired_external_job_becomes_manual_resume_failure(self) -> None:
        document_id = self.add_document(build_scanned_pdf())
        dispatcher = RecordingDispatcher()
        with self.Session() as db:
            self.service(db, dispatcher).process_document(document_id)
            document = db.get(Document, document_id)
            assert document is not None
            document.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()
            self.service(db, dispatcher).fail_stale_documents(document_id)
            failed = db.get(Document, document_id)
            assert failed is not None
            self.assertEqual(failed.processing_status, DocumentProcessingStatus.FAILED)
            self.assertIn("云端 OCR", failed.error_message or "")

    def test_dispatch_failure_stops_and_waits_for_manual_resume(self) -> None:
        document_id = self.add_document(build_scanned_pdf())
        dispatcher = RecordingDispatcher(GitHubOCRDispatchError("GitHub OCR 工作流不存在或尚未启用"))
        with self.Session() as db:
            failed = self.service(db, dispatcher).process_document(document_id)
            self.assertEqual(failed.processing_status, DocumentProcessingStatus.FAILED)
            self.assertEqual(failed.processing_phase, "failed")
            self.assertIn("工作流", failed.error_message or "")
            self.assertIsNone(failed.lease_owner)
            self.assertIsNotNone(failed.storage_key)

    def test_worker_claim_page_results_and_finish_are_incremental(self) -> None:
        content = build_scanned_pdf(2)
        document_id = self.add_document(content, status=DocumentProcessingStatus.PROCESSING)
        attempt_id = uuid.uuid4()
        with self.Session() as db:
            document = db.get(Document, document_id)
            assert document is not None
            document.lease_owner = f"github:{attempt_id}"
            document.processing_phase = "external_ocr_queued"
            db.commit()
            processing = self.service(db)
            jobs = GitHubOCRJobService(
                db=db,
                storage=self.storage,
                processing=processing,
                lease_seconds=300,
                download_url_expires_seconds=300,
                ocr_dpi=144,
                minimum_text_length=8,
                max_pdf_pages=600,
            )
            claim = jobs.claim(document_id, attempt_id)
            self.assertEqual(claim.completed_pages, [])
            self.assertTrue(claim.download_url.startswith("file:"))
            progress = jobs.complete_page(document_id, attempt_id, 1, text="第一页", extraction_method=ExtractionMethod.OCR)
            self.assertEqual(progress.processed_pages, 1)
            repeated = jobs.complete_page(document_id, attempt_id, 1, text="不应覆盖", extraction_method=ExtractionMethod.TEXT)
            self.assertEqual(repeated.processed_pages, 1)
            stored_page = db.scalar(select(DocumentPage).where(DocumentPage.document_id == document_id, DocumentPage.page_number == 1))
            assert stored_page is not None
            self.assertEqual(stored_page.extracted_text, "第一页")
            self.assertEqual(stored_page.extraction_method, ExtractionMethod.OCR)
            jobs.complete_page(document_id, attempt_id, 2, text="第二页", extraction_method=ExtractionMethod.OCR)
            completed = jobs.finish(
                document_id,
                attempt_id,
                outline=(SourceOutlineEntry(level=1, title="第一章", page_number=1),),
            )
            self.assertEqual(completed.processing_status, DocumentProcessingStatus.PARSED)
            self.assertEqual(completed.processed_pages, 2)
            self.assertEqual(completed.ocr_page_count, 2)
            self.assertIsNotNone(db.scalar(select(ParsedDocument).where(ParsedDocument.document_id == document_id)))
            self.assertIsNone(completed.storage_key)

    def test_stale_attempt_cannot_overwrite_newer_job(self) -> None:
        document_id = self.add_document(build_scanned_pdf(), status=DocumentProcessingStatus.PROCESSING)
        current_attempt, stale_attempt = uuid.uuid4(), uuid.uuid4()
        with self.Session() as db:
            document = db.get(Document, document_id)
            assert document is not None
            document.lease_owner = f"github:{current_attempt}"
            document.processing_phase = "external_ocr_processing"
            db.commit()
            jobs = GitHubOCRJobService(
                db=db, storage=self.storage, processing=self.service(db), lease_seconds=300,
                download_url_expires_seconds=300, ocr_dpi=144, minimum_text_length=8, max_pdf_pages=600,
            )
            with self.assertRaises(GitHubOCRJobConflictError):
                jobs.complete_page(document_id, stale_attempt, 1, text="stale", extraction_method=ExtractionMethod.OCR)


if __name__ == "__main__":
    unittest.main()
