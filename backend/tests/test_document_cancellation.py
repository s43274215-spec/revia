import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.document.parser import PDFParser
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.main import app
from app.models.content import Chapter
from app.models.document import DocumentPage, ParsedDocument, TextChunk
from app.models.enums import (
    DocumentKind,
    DocumentPageStatus,
    DocumentProcessingStatus,
    ExtractionMethod,
)
from app.models.project import Document, Project
from app.models.workspace import Workspace
from app.services.document_processing import DocumentProcessingService, LeaseUnavailableError
from app.services.storage import LocalStorageProvider, build_object_key
from tests.helpers import authorization_header


class FailingDeleteStorage(LocalStorageProvider):
    def delete_object(self, object_key: str) -> None:
        raise RuntimeError("simulated object deletion failure")


class DocumentCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.workspace_id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.document_id = uuid.uuid4()
        self.object_key = build_object_key(self.workspace_id, self.document_id)

        with self.Session() as db:
            db.add(Workspace(id=self.workspace_id))
            db.add(Project(id=self.project_id, workspace_id=self.workspace_id, name="人力资源"))
            db.flush()
            db.add(Document(
                id=self.document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="1.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
                storage_key=self.object_key,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.INTERRUPTED,
                processing_phase="resource_limited",
                total_pages=100,
                processed_pages=56,
                current_page=57,
                lease_owner="expired-worker",
                lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
                retry_not_before=datetime.now(UTC) + timedelta(hours=1),
                accepted_at=datetime.now(UTC),
                quota_pages=100,
            ))
            db.add(DocumentPage(
                document_id=self.document_id,
                page_number=1,
                status=DocumentPageStatus.COMPLETED,
                extraction_method=ExtractionMethod.OCR,
                extracted_text="已完成页面",
                character_count=5,
            ))
            parsed = ParsedDocument(
                document_id=self.document_id,
                page_count=1,
                raw_text="已整理内容",
                parser_name="pymupdf",
                parser_version="test",
                is_scanned=True,
                ocr_executed=True,
                ocr_page_count=1,
            )
            db.add(parsed)
            db.flush()
            db.add(TextChunk(
                parsed_document_id=parsed.id,
                position=0,
                page_start=1,
                page_end=1,
                chapter_title="第一章",
                section_title=None,
                content="已整理内容",
            ))
            db.add(Chapter(project_id=self.project_id, title="已生成章节", position=0))
            db.commit()

        object_path = Path(self.storage.name, self.object_key)
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(b"temporary pdf")

        def override_db():
            with self.Session() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            database_url="sqlite+pysqlite:///:memory:",
            file_storage_root=self.storage.name,
            public_access_enabled=True,
        )
        self.client = TestClient(app)
        self.client.headers.update(authorization_header(self.workspace_id))

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.storage.cleanup()

    def _service(self, db, storage_class=LocalStorageProvider) -> DocumentProcessingService:
        storage = storage_class(Path(self.storage.name), max_upload_bytes=150 * 1024 * 1024)
        return DocumentProcessingService(
            db,
            storage,
            PDFParser(ocr_enabled=False),
            TextStructurer(),
            StructuredTextSplitter(),
        )

    def test_cancel_interrupted_document_is_idempotent_and_releases_active_slot(self) -> None:
        endpoint = f"/api/v1/projects/{self.project_id}/documents/{self.document_id}/cancel"
        first = self.client.post(endpoint)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["processing_status"], "cancelled")
        self.assertEqual(first.json()["processing_phase"], "user_cancelled")
        self.assertIsNotNone(first.json()["cancelled_at"])

        repeated = self.client.post(endpoint)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["processing_status"], "cancelled")
        self.assertIsNone(self.client.get("/api/v1/projects/active-document").json())

        with self.Session() as db:
            document = db.get(Document, self.document_id)
            assert document is not None
            self.assertEqual(document.processing_status, DocumentProcessingStatus.CANCELLED)
            self.assertEqual(document.processing_phase, "user_cancelled")
            self.assertIsNone(document.lease_owner)
            self.assertIsNone(document.lease_expires_at)
            self.assertIsNone(document.retry_not_before)
            self.assertIsNone(document.storage_key)
            self.assertEqual(db.scalar(select(func.count(DocumentPage.id))), 1)
            self.assertEqual(db.scalar(select(func.count(TextChunk.id))), 1)
            self.assertEqual(db.scalar(select(func.count(Chapter.id))), 1)
            self.assertIsNone(self._service(db).claim_next("queue-worker"))

        self.assertFalse(Path(self.storage.name, self.object_key).exists())
        new_upload = self.client.post(
            f"/api/v1/projects/{self.project_id}/documents/uploads",
            json={
                "kind": "course_material",
                "filename": "diagnostic.pdf",
                "content_type": "application/pdf",
                "size_bytes": 1024,
            },
        )
        self.assertEqual(new_upload.status_code, 201, new_upload.text)

    def test_other_workspace_cannot_cancel_document(self) -> None:
        other_workspace_id = uuid.uuid4()
        with self.Session() as db:
            db.add(Workspace(id=other_workspace_id))
            db.commit()
        response = self.client.post(
            f"/api/v1/projects/{self.project_id}/documents/{self.document_id}/cancel",
            headers=authorization_header(other_workspace_id),
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_queued_document_can_be_cancelled(self) -> None:
        with self.Session() as db:
            document = db.get(Document, self.document_id)
            assert document is not None
            document.processing_status = DocumentProcessingStatus.QUEUED
            document.processing_phase = "queued"
            document.lease_owner = None
            document.lease_expires_at = None
            db.commit()

        response = self.client.post(
            f"/api/v1/projects/{self.project_id}/documents/{self.document_id}/cancel"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["processing_status"], "cancelled")
        self.assertIsNone(self.client.get("/api/v1/projects/active-document").json())

    def test_processing_cancellation_stops_at_next_lease_checkpoint(self) -> None:
        owner = "active-worker"
        with self.Session() as db:
            document = db.get(Document, self.document_id)
            assert document is not None
            document.processing_status = DocumentProcessingStatus.PROCESSING
            document.processing_phase = "extracting"
            document.lease_owner = owner
            document.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
            document.retry_not_before = None
            db.commit()

        response = self.client.post(
            f"/api/v1/projects/{self.project_id}/documents/{self.document_id}/cancel"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["processing_status"], "cancelled")
        self.assertIsNone(self.client.get("/api/v1/projects/active-document").json())

        with self.Session() as db:
            document = db.get(Document, self.document_id)
            assert document is not None
            self.assertEqual(document.lease_owner, owner)
            service = self._service(db)
            self.assertIsNone(service.claim_next("second-worker"))
            with self.assertRaises(LeaseUnavailableError):
                service._renew_lease(self.document_id, owner)
            db.refresh(document)
            self.assertEqual(document.processing_status, DocumentProcessingStatus.CANCELLED)
            self.assertIsNone(document.lease_owner)
            self.assertIsNone(document.lease_expires_at)

    def test_non_resource_failure_uses_retry_backoff(self) -> None:
        owner = "failing-worker"
        with self.Session() as db:
            document = db.get(Document, self.document_id)
            assert document is not None
            document.processing_status = DocumentProcessingStatus.PROCESSING
            document.processing_phase = "extracting"
            document.lease_owner = owner
            document.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
            document.retry_not_before = None
            db.commit()
            self._service(db)._mark_interrupted(self.document_id, owner, RuntimeError("safe parse failure"))
            db.refresh(document)
            self.assertEqual(document.processing_status, DocumentProcessingStatus.INTERRUPTED)
            self.assertEqual(document.processing_phase, "interrupted")
            self.assertIsNotNone(document.retry_not_before)
            self.assertEqual(document.error_message, "safe parse failure")

    def test_object_delete_failure_does_not_rollback_cancellation(self) -> None:
        with self.Session() as db:
            document = self._service(db, FailingDeleteStorage).cancel_document(
                self.workspace_id,
                self.project_id,
                self.document_id,
            )
            self.assertEqual(document.processing_status, DocumentProcessingStatus.CANCELLED)
            self.assertEqual(document.processing_phase, "user_cancelled")
            self.assertEqual(document.storage_key, self.object_key)
            self.assertIsNone(self._service(db, FailingDeleteStorage).claim_next("queue-worker"))


if __name__ == "__main__":
    unittest.main()
