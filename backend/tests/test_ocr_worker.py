import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.memory import process_rss_mb
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.document.ocr import OCRPageResult, OCRWorkerClient, OCRWorkerResourceError
from app.document.parser import PDFParser
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.models.document import DocumentPage, ParsedDocument, TextChunk
from app.models.enums import DocumentKind, DocumentProcessingStatus
from app.models.project import Document, Project
from app.models.workspace import Workspace
from app.services.document_processing import DocumentProcessingError, DocumentProcessingService
from app.services.storage import LocalStorageProvider, build_object_key
from app.main import app
from tests.helpers import authorization_header


def build_scanned_pdf(page_count: int = 3) -> bytes:
    image = Image.new("L", (1200, 800), "white")
    ImageDraw.Draw(image).text((80, 100), "Human resource management course material", fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    image_bytes = output.getvalue()
    document = fitz.open()
    for _ in range(page_count):
        page = document.new_page(width=1200, height=800)
        page.insert_image(page.rect, stream=image_bytes)
    content = document.tobytes(garbage=4, deflate=True)
    document.close()
    image.close()
    return content


def build_text_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Chapter One\nThis PDF already has a valid text layer for extraction.")
    content = document.tobytes()
    document.close()
    return content


class OCRWorkerIsolationTests(unittest.TestCase):
    def test_fastapi_startup_does_not_import_rapidocr_or_onnxruntime(self) -> None:
        backend_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import app.main; "
                    "assert 'rapidocr' not in sys.modules; "
                    "assert 'onnxruntime' not in sys.modules; "
                    "print('OCR_STARTUP_IMPORTS=clean')"
                ),
            ],
            cwd=backend_root,
            env={**os.environ, "OCR_ENABLED": "true"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("OCR_STARTUP_IMPORTS=clean", completed.stdout)

    def test_text_pdf_never_starts_ocr_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.pdf"
            path.write_bytes(build_text_pdf())
            parsed = PDFParser(max_pages=600).parse(path)
        self.assertFalse(parsed.ocr_executed)
        self.assertNotIn("rapidocr", sys.modules)
        self.assertNotIn("onnxruntime", sys.modules)

    def test_worker_recycles_after_soft_rss_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scanned.pdf"
            path.write_bytes(build_scanned_pdf(page_count=1))
            client = OCRWorkerClient(max_rss_mb=1, threads=1, timeout_seconds=180)
            try:
                result = client.recognize_page(path, 1, 144)
                self.assertTrue(result.initialized)
                self.assertFalse(client.initialized)
            finally:
                client.close()

    def test_worker_retires_after_configured_page_budget(self) -> None:
        client = OCRWorkerClient(
            max_rss_mb=300,
            max_pages=1,
            container_memory_budget_mb=900,
            threads=1,
            timeout_seconds=180,
        )

        class RunningProcess:
            pid = os.getpid()
            exitcode = None

            @staticmethod
            def is_alive() -> bool:
                return True

        class ResultConnection:
            @staticmethod
            def send(message) -> None:
                return None

            @staticmethod
            def poll(timeout=None) -> bool:
                return True

            @staticmethod
            def recv():
                return {
                    "event": "result",
                    "ok": True,
                    "page_number": 1,
                    "text": "page text",
                    "character_count": 9,
                    "rss_mb": 180.0,
                    "baseline_rss_mb": 70.0,
                    "engine_rss_mb": 160.0,
                    "render_rss_mb": 170.0,
                    "initialized": True,
                    "engine_version": "test",
                    "retire_after_page": False,
                }

        client._process = RunningProcess()
        client._connection = ResultConnection()
        client._ensure_started = lambda page_number: None
        with (
            patch("app.document.ocr.process_rss_mb", return_value=80.0),
            patch("app.document.ocr.container_memory_mb", return_value=220.0),
            patch.object(client, "close") as close,
        ):
            result = client.recognize_page(Path("private.pdf"), 1, 144)

        self.assertEqual(result.text, "page text")
        self.assertEqual(result.container_peak_rss_mb, 220.0)
        close.assert_called_once_with()

    def test_worker_stops_before_container_oom_limit(self) -> None:
        client = OCRWorkerClient(
            max_rss_mb=300,
            max_pages=10,
            container_memory_budget_mb=480,
            threads=1,
            timeout_seconds=180,
        )

        class RunningProcess:
            pid = os.getpid()
            exitcode = None

            @staticmethod
            def is_alive() -> bool:
                return True

        class WaitingConnection:
            @staticmethod
            def send(message) -> None:
                return None

            @staticmethod
            def poll(timeout=None) -> bool:
                return False

        client._process = RunningProcess()
        client._connection = WaitingConnection()
        client._ensure_started = lambda page_number: None
        with (
            patch("app.document.ocr.process_rss_mb", return_value=280.0),
            patch("app.document.ocr.container_memory_mb", return_value=481.0),
            patch.object(client, "close") as close,
            self.assertLogs("revia.ocr.worker", level="ERROR") as captured,
        ):
            with self.assertRaisesRegex(OCRWorkerResourceError, "container_memory_limit"):
                client.recognize_page(Path("private.pdf"), 7, 144)

        close.assert_called_once_with(force=True)
        diagnostic = "\n".join(captured.output)
        self.assertIn("reason=container_memory_limit", diagnostic)
        self.assertIn("container_peak_rss_mb=481.0", diagnostic)
        self.assertNotIn("private.pdf", diagnostic)

    def test_real_worker_isolated_memory_stays_below_render_budget(self) -> None:
        self.assertNotIn("rapidocr", sys.modules)
        self.assertNotIn("onnxruntime", sys.modules)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scanned.pdf"
            path.write_bytes(build_scanned_pdf())
            parent_rss = process_rss_mb()
            client = OCRWorkerClient(max_rss_mb=300, threads=1, timeout_seconds=180)
            try:
                results = [client.recognize_page(path, page_number, 144) for page_number in range(1, 4)]
            finally:
                client.close()

        worker_peaks = [result.worker_peak_rss_mb for result in results]
        worker_final = [result.worker_rss_mb for result in results]
        first = results[0]
        metrics = {
            "parent_rss_mb": round(parent_rss, 1),
            "worker_baseline_rss_mb": round(first.worker_baseline_rss_mb, 1),
            "engine_initialized_rss_mb": round(first.engine_initialized_rss_mb, 1),
            "page_rendered_rss_mb": round(first.page_rendered_rss_mb, 1),
            "worker_peak_rss_mb": round(max(worker_peaks), 1),
            "combined_peak_mb": round(parent_rss + max(worker_peaks), 1),
        }
        print("OCR_MEMORY_RESULT=" + json.dumps(metrics, ensure_ascii=False))
        self.assertTrue(all(result.initialized for result in results))
        self.assertLess(max(worker_peaks), 300)
        self.assertLess(parent_rss + max(worker_peaks), 512)
        self.assertLessEqual(worker_final[-1], worker_final[0] + 32)
        self.assertNotIn("rapidocr", sys.modules)
        self.assertNotIn("onnxruntime", sys.modules)
        self.assertFalse(client.initialized)

    def test_worker_failure_log_contains_only_safe_diagnostics(self) -> None:
        client = OCRWorkerClient(max_rss_mb=300, threads=1, timeout_seconds=180)

        class ExitedProcess:
            pid = os.getpid()
            exitcode = -9

            @staticmethod
            def is_alive() -> bool:
                return False

            @staticmethod
            def join(timeout=None) -> None:
                return None

        class StageThenEOFConnection:
            def __init__(self) -> None:
                self.receives = 0

            @staticmethod
            def send(message) -> None:
                return None

            @staticmethod
            def poll(timeout=None) -> bool:
                return True

            def recv(self):
                self.receives += 1
                if self.receives == 1:
                    return {
                        "event": "stage",
                        "stage": "worker_engine_initializing",
                        "rss_mb": 271.5,
                    }
                raise EOFError("private source path must not be logged")

            @staticmethod
            def close() -> None:
                return None

        client._process = ExitedProcess()
        client._connection = StageThenEOFConnection()
        client._ensure_started = lambda page_number: None
        with self.assertLogs("revia.ocr.worker", level="ERROR") as captured:
            with self.assertRaises(OCRWorkerResourceError):
                client.recognize_page(Path("private-course.pdf"), 1, 144)

        diagnostic = "\n".join(captured.output)
        self.assertIn("page=1", diagnostic)
        self.assertIn("exit_code=-9", diagnostic)
        self.assertIn("timeout=false", diagnostic)
        self.assertIn("broken_pipe=true", diagnostic)
        self.assertIn("last_stage=worker_engine_initializing", diagnostic)
        self.assertIn("peak_rss_mb=271.5", diagnostic)
        self.assertNotIn("private-course.pdf", diagnostic)
        self.assertNotIn("private source path", diagnostic)

    def test_worker_timeout_log_is_distinguished_from_broken_pipe(self) -> None:
        client = OCRWorkerClient(max_rss_mb=300, threads=1, timeout_seconds=1)

        class RunningProcess:
            pid = os.getpid()
            exitcode = None
            alive = True

            def is_alive(self) -> bool:
                return self.alive

            @staticmethod
            def join(timeout=None) -> None:
                return None

            def terminate(self) -> None:
                self.alive = False
                self.exitcode = -15

        class SilentConnection:
            @staticmethod
            def send(message) -> None:
                return None

            @staticmethod
            def poll(timeout=None) -> bool:
                return False

            @staticmethod
            def close() -> None:
                return None

        client._process = RunningProcess()
        client._connection = SilentConnection()
        client._ensure_started = lambda page_number: None
        with self.assertLogs("revia.ocr.worker", level="ERROR") as captured:
            with self.assertRaises(OCRWorkerResourceError):
                client.recognize_page(Path("private-course.pdf"), 7, 144)

        diagnostic = "\n".join(captured.output)
        self.assertIn("page=7", diagnostic)
        self.assertIn("reason=timeout", diagnostic)
        self.assertIn("timeout=true", diagnostic)
        self.assertIn("broken_pipe=false", diagnostic)
        self.assertIn("last_stage=worker_started", diagnostic)
        self.assertNotIn("private-course.pdf", diagnostic)


class FailingOCRWorker:
    def recognize_page(self, path: Path, page_number: int, dpi: int) -> OCRPageResult:
        raise OCRWorkerResourceError("simulated worker termination")

    def close(self) -> None:
        pass


class SuccessfulOCRWorker:
    def recognize_page(self, path: Path, page_number: int, dpi: int) -> OCRPageResult:
        text = "第一章 人力资源管理\n人力资源规划、招聘、培训、绩效、薪酬与员工关系。"
        return OCRPageResult(
            page_number=page_number,
            text=text,
            character_count=len(text),
            worker_rss_mb=180.0,
            worker_peak_rss_mb=230.0,
            worker_baseline_rss_mb=75.0,
            engine_initialized_rss_mb=170.0,
            page_rendered_rss_mb=175.0,
            initialized=True,
            engine_version="test",
        )

    def close(self) -> None:
        pass


class SlowOCRWorker(SuccessfulOCRWorker):
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release

    def recognize_page(self, path: Path, page_number: int, dpi: int) -> OCRPageResult:
        self.started.set()
        if not self.release.wait(timeout=10):
            raise OCRWorkerResourceError("slow worker timed out")
        return super().recognize_page(path, page_number, dpi)


class RecordingOCRWorker(SuccessfulOCRWorker):
    def __init__(self, *, fail_on_page: int | None = None) -> None:
        self.calls: list[int] = []
        self.fail_on_page = fail_on_page

    def recognize_page(self, path: Path, page_number: int, dpi: int) -> OCRPageResult:
        self.calls.append(page_number)
        if page_number == self.fail_on_page:
            raise OCRWorkerResourceError("simulated worker termination")
        return super().recognize_page(path, page_number, dpi)


class ParentPDFReleaseProbeParser(PDFParser):
    def __init__(self, worker: RecordingOCRWorker) -> None:
        super().__init__(max_pages=600, ocr_worker=worker)
        self.parent_document_ids: list[int] = []
        self.parent_closed_before_ocr: list[bool] = []
        self._inspected_document = None

    def extract_text_page(self, page: fitz.Page, page_number: int):
        self._inspected_document = page.parent
        self.parent_document_ids.append(id(page.parent))
        return super().extract_text_page(page, page_number)

    def extract_ocr_page(self, page_number: int, **kwargs):
        assert self._inspected_document is not None
        self.parent_closed_before_ocr.append(bool(self._inspected_document.is_closed))
        self._inspected_document = None
        return super().extract_ocr_page(page_number, **kwargs)


class OCRResourceRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage_directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.workspace_id, self.project_id, self.document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.storage = LocalStorageProvider(Path(self.storage_directory.name), max_upload_bytes=10 * 1024 * 1024)
        object_key = build_object_key(self.workspace_id, self.document_id)
        path = self.storage.resolve(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = build_scanned_pdf(page_count=1)
        path.write_bytes(content)
        with self.Session() as db:
            db.add(Workspace(id=self.workspace_id))
            db.add(Project(id=self.project_id, workspace_id=self.workspace_id, name="OCR 恢复测试"))
            db.add(Document(
                id=self.document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="scanned.pdf",
                mime_type="application/pdf",
                size_bytes=len(content),
                storage_key=object_key,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.QUEUED,
                processing_phase="queued",
            ))
            db.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.storage_directory.cleanup()

    def _service(self, db, worker=None, *, ocr_enabled: bool = True) -> DocumentProcessingService:
        return DocumentProcessingService(
            db,
            self.storage,
            PDFParser(max_pages=600, ocr_enabled=ocr_enabled, ocr_worker=worker),
            TextStructurer(),
            StructuredTextSplitter(),
            lease_seconds=30,
        )

    def test_resource_failure_waits_for_manual_resume_without_duplicates(self) -> None:
        with self.Session() as db:
            for attempt in range(3):
                if attempt:
                    self._service(db, FailingOCRWorker()).resume_failed_document(
                        self.workspace_id, self.project_id, self.document_id
                    )
                with self.assertRaisesRegex(DocumentProcessingError, "OCR 处理因服务器资源不足暂停"):
                    self._service(db, FailingOCRWorker()).process_document(self.document_id)

            document = db.get(Document, self.document_id)
            assert document is not None
            self.assertEqual(document.processing_status, DocumentProcessingStatus.FAILED)
            self.assertEqual(document.processing_phase, "failed")
            self.assertEqual(document.error_message, "OCR 处理因服务器资源不足停止，请稍后手动点击“继续识别”。")
            self.assertIsNone(document.retry_not_before)
            self.assertIsNotNone(document.storage_key)
            page = db.scalar(select(DocumentPage).where(DocumentPage.document_id == self.document_id))
            assert page is not None
            self.assertEqual(page.retry_count, 2)
            self.assertIsNone(self._service(db, FailingOCRWorker()).claim_next("automatic-worker"))

            successful_service = self._service(db, SuccessfulOCRWorker())
            successful_service.resume_failed_document(self.workspace_id, self.project_id, self.document_id)
            completed = successful_service.process_document(self.document_id)
            self.assertEqual(completed.processing_status, DocumentProcessingStatus.PARSED)
            self.assertEqual(completed.processed_pages, 1)
            self.assertEqual(completed.ocr_page_count, 1)
            self.assertIsNone(completed.storage_key)
            self.assertEqual(
                db.scalar(select(func.count(DocumentPage.id)).where(DocumentPage.document_id == self.document_id)),
                1,
            )
            parsed_id = db.scalar(select(ParsedDocument.id).where(ParsedDocument.document_id == self.document_id))
            self.assertIsNotNone(parsed_id)
            self.assertGreater(
                db.scalar(select(func.count(TextChunk.id)).where(TextChunk.parsed_document_id == parsed_id)),
                0,
            )

    def test_ocr_disabled_waits_for_manual_resume_without_duplicate_pages_or_chunks(self) -> None:
        with self.Session() as db:
            for attempt in range(4):
                if attempt:
                    self._service(db, ocr_enabled=False).resume_failed_document(
                        self.workspace_id, self.project_id, self.document_id
                    )
                with self.assertRaisesRegex(DocumentProcessingError, "ocr_disabled"):
                    self._service(db, ocr_enabled=False).process_document(self.document_id)

            document = db.get(Document, self.document_id)
            assert document is not None
            self.assertEqual(document.processing_status, DocumentProcessingStatus.FAILED)
            self.assertEqual(document.processing_phase, "failed")
            self.assertEqual(document.error_message, "ocr_disabled")
            self.assertIsNone(document.retry_not_before)
            self.assertIsNotNone(document.storage_key)

            page = db.scalar(select(DocumentPage).where(DocumentPage.document_id == self.document_id))
            assert page is not None
            self.assertNotEqual(page.status.value, "completed")

            successful_service = self._service(db, SuccessfulOCRWorker())
            successful_service.resume_failed_document(self.workspace_id, self.project_id, self.document_id)
            completed = successful_service.process_document(self.document_id)
            self.assertEqual(completed.processing_status, DocumentProcessingStatus.PARSED)
            self.assertEqual(completed.processed_pages, 1)
            self.assertEqual(
                db.scalar(select(func.count(DocumentPage.id)).where(DocumentPage.document_id == self.document_id)),
                1,
            )
            parsed_id = db.scalar(select(ParsedDocument.id).where(ParsedDocument.document_id == self.document_id))
            assert parsed_id is not None
            chunk_count = db.scalar(
                select(func.count(TextChunk.id)).where(TextChunk.parsed_document_id == parsed_id)
            )
            self.assertGreater(chunk_count, 0)

            repeated = self._service(db, SuccessfulOCRWorker()).process_document(self.document_id)
            self.assertEqual(repeated.id, completed.id)
            self.assertEqual(
                db.scalar(select(func.count(DocumentPage.id)).where(DocumentPage.document_id == self.document_id)),
                1,
            )
            self.assertEqual(
                db.scalar(select(func.count(TextChunk.id)).where(TextChunk.parsed_document_id == parsed_id)),
                chunk_count,
            )

    def test_text_pdf_completes_while_ocr_is_disabled(self) -> None:
        text_document_id = uuid.uuid4()
        object_key = build_object_key(self.workspace_id, text_document_id)
        path = self.storage.resolve(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = build_text_pdf()
        path.write_bytes(content)
        with self.Session() as db:
            db.add(Document(
                id=text_document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="text.pdf",
                mime_type="application/pdf",
                size_bytes=len(content),
                storage_key=object_key,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.QUEUED,
                processing_phase="queued",
            ))
            db.commit()

            completed = self._service(db, ocr_enabled=False).process_document(text_document_id)
            self.assertEqual(completed.processing_status, DocumentProcessingStatus.PARSED)
            self.assertEqual(completed.ocr_page_count, 0)

    def test_parent_pdf_closes_before_ocr_and_resume_reopens_without_duplicates(self) -> None:
        content = build_scanned_pdf(page_count=2)
        path = self.storage.resolve(build_object_key(self.workspace_id, self.document_id))
        path.write_bytes(content)
        with self.Session() as db:
            document = db.get(Document, self.document_id)
            assert document is not None
            document.size_bytes = len(content)
            db.commit()

            failing_worker = RecordingOCRWorker(fail_on_page=2)
            first_parser = ParentPDFReleaseProbeParser(failing_worker)
            first_service = DocumentProcessingService(
                db,
                self.storage,
                first_parser,
                TextStructurer(),
                StructuredTextSplitter(),
                lease_seconds=30,
            )
            with self.assertRaises(DocumentProcessingError):
                first_service.process_document(self.document_id)

            interrupted = db.get(Document, self.document_id)
            assert interrupted is not None
            self.assertEqual(interrupted.processing_status, DocumentProcessingStatus.FAILED)
            self.assertEqual(interrupted.processing_phase, "failed")
            self.assertIsNone(interrupted.retry_not_before)
            self.assertEqual(interrupted.processed_pages, 1)
            self.assertEqual(failing_worker.calls, [1, 2])
            self.assertEqual(first_parser.parent_closed_before_ocr, [True, True])
            self.assertEqual(len(set(first_parser.parent_document_ids)), 2)

            resumed_worker = RecordingOCRWorker()
            resumed_parser = ParentPDFReleaseProbeParser(resumed_worker)
            resumed_service = DocumentProcessingService(
                db,
                self.storage,
                resumed_parser,
                TextStructurer(),
                StructuredTextSplitter(),
                lease_seconds=30,
            )
            resumed_service.resume_failed_document(self.workspace_id, self.project_id, self.document_id)
            completed = resumed_service.process_document(self.document_id)
            self.assertEqual(completed.processing_status, DocumentProcessingStatus.PARSED)
            self.assertEqual(completed.processed_pages, 2)
            self.assertEqual(resumed_worker.calls, [2])
            self.assertEqual(resumed_parser.parent_closed_before_ocr, [True])
            self.assertEqual(
                db.scalar(select(func.count(DocumentPage.id)).where(DocumentPage.document_id == self.document_id)),
                2,
            )
            parsed_id = db.scalar(select(ParsedDocument.id).where(ParsedDocument.document_id == self.document_id))
            assert parsed_id is not None
            chunk_count = db.scalar(
                select(func.count(TextChunk.id)).where(TextChunk.parsed_document_id == parsed_id)
            )
            self.assertGreater(chunk_count, 0)

            resumed_service.process_document(self.document_id)
            self.assertEqual(resumed_worker.calls, [2])
            self.assertEqual(
                db.scalar(select(func.count(DocumentPage.id)).where(DocumentPage.document_id == self.document_id)),
                2,
            )
            self.assertEqual(
                db.scalar(select(func.count(TextChunk.id)).where(TextChunk.parsed_document_id == parsed_id)),
                chunk_count,
            )


class OCRWebResponsivenessTests(unittest.TestCase):
    def test_health_and_progress_stay_available_while_ocr_waits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "ocr-health.sqlite3"
            engine = create_engine(
                f"sqlite+pysqlite:///{database_path.as_posix()}",
                connect_args={"check_same_thread": False},
            )
            Session = sessionmaker(bind=engine, expire_on_commit=False)
            Base.metadata.create_all(engine)
            workspace_id, project_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            storage = LocalStorageProvider(Path(directory) / "storage", max_upload_bytes=10 * 1024 * 1024)
            object_key = build_object_key(workspace_id, document_id)
            path = storage.resolve(object_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = build_scanned_pdf(page_count=1)
            path.write_bytes(content)
            with Session() as db:
                db.add(Workspace(id=workspace_id))
                db.add(Project(id=project_id, workspace_id=workspace_id, name="OCR 健康检查"))
                db.add(Document(
                    id=document_id,
                    project_id=project_id,
                    kind=DocumentKind.COURSE_MATERIAL,
                    original_name="health.pdf",
                    mime_type="application/pdf",
                    size_bytes=len(content),
                    storage_key=object_key,
                    storage_backend="local",
                    processing_status=DocumentProcessingStatus.QUEUED,
                    processing_phase="queued",
                ))
                db.commit()

            settings = Settings(
                _env_file=None,
                database_url=f"sqlite+pysqlite:///{database_path.as_posix()}",
                file_storage_root=str(Path(directory) / "storage"),
                public_access_enabled=True,
            )

            def override_db():
                with Session() as db:
                    yield db

            app.dependency_overrides[get_db] = override_db
            app.dependency_overrides[get_settings] = lambda: settings
            client = TestClient(app)
            client.headers.update(authorization_header(workspace_id, settings))
            started, release = threading.Event(), threading.Event()
            errors: list[Exception] = []

            def process() -> None:
                try:
                    with Session() as db:
                        DocumentProcessingService(
                            db,
                            storage,
                            PDFParser(max_pages=600, ocr_worker=SlowOCRWorker(started, release)),
                            TextStructurer(),
                            StructuredTextSplitter(),
                            lease_seconds=30,
                        ).process_document(document_id)
                except Exception as exc:  # pragma: no cover - assertion reports the exception
                    errors.append(exc)

            thread = threading.Thread(target=process, daemon=True)
            thread.start()
            self.assertTrue(started.wait(timeout=5))
            health = client.get("/health")
            progress = client.get(f"/api/v1/projects/{project_id}/documents/{document_id}")
            self.assertEqual(health.status_code, 200, health.text)
            self.assertEqual(progress.status_code, 200, progress.text)
            self.assertEqual(progress.json()["processing_status"], "processing")
            release.set()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            client.close()
            app.dependency_overrides.clear()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
