import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import fitz
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.auth.security import SessionTokenSigner
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.document.parser import PDFParser
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.main import app
from app.models.enums import DocumentKind, DocumentProcessingStatus
from app.models.project import Document, Project
from app.models.workspace import Workspace
from app.services.document_processing import DocumentProcessingError, DocumentProcessingService, DocumentQuotaError
from app.services.storage import LocalStorageProvider, UploadAuthorizationError, build_object_key, validate_object_scope


def make_pdf(page_count: int) -> bytes:
    pdf = fitz.open()
    for number in range(1, page_count + 1):
        page = pdf.new_page()
        page.insert_text((36, 36), f"Chapter One\nPage {number} structured course content.")
    data = pdf.tobytes(garbage=4, deflate=True)
    pdf.close()
    return data


class PublicBetaQuotaQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.storage = LocalStorageProvider(Path(self.temp.name), max_upload_bytes=150 * 1024 * 1024)
        self.workspace_id, self.project_id = self._workspace_project("额度测试")

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def _workspace_project(self, name: str) -> tuple[uuid.UUID, uuid.UUID]:
        workspace_id, project_id = uuid.uuid4(), uuid.uuid4()
        with self.Session() as db:
            db.add(Workspace(id=workspace_id))
            db.add(Project(id=project_id, workspace_id=workspace_id, name=name))
            db.commit()
        return workspace_id, project_id

    def _service(self, db, **limits: int) -> DocumentProcessingService:
        return DocumentProcessingService(
            db,
            self.storage,
            PDFParser(max_pages=600),
            TextStructurer(),
            StructuredTextSplitter(),
            workspace_max_active_documents=limits.get("workspace_active", 1),
            workspace_rolling_24h_page_limit=limits.get("workspace_pages", 1200),
            global_max_processing_documents=limits.get("global_processing", 1),
            global_rolling_24h_page_limit=limits.get("global_pages", 3000),
        )

    def _upload(self, db, workspace_id: uuid.UUID, project_id: uuid.UUID, pages: int, **limits: int) -> tuple[DocumentProcessingService, Document]:
        service = self._service(db, **limits)
        data = make_pdf(pages)
        document, _ = service.create_upload(
            workspace_id,
            project_id,
            DocumentKind.COURSE_MATERIAL,
            filename=f"{pages}.pdf",
            content_type="application/pdf",
            size_bytes=len(data),
            upload_endpoint="http://local.invalid/{document_id}",
        )
        assert document.storage_key is not None
        path = self.storage.resolve(document.storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return service, document

    def _finish_without_processing(self, db, document: Document) -> None:
        if document.storage_key:
            self.storage.delete_object(document.storage_key)
        document.storage_key = None
        document.processing_status = DocumentProcessingStatus.PARSED
        document.processing_phase = "completed"
        db.commit()

    def test_active_limit_and_four_300_page_documents_fill_rolling_limit(self) -> None:
        with self.Session() as db:
            service, first = self._upload(db, self.workspace_id, self.project_id, 300)
            accepted = service.confirm_upload(self.workspace_id, self.project_id, first.id)
            self.assertEqual(accepted.total_pages, 300)
            with self.assertRaisesRegex(DocumentQuotaError, "正在排队或处理中"):
                service.create_upload(
                    self.workspace_id,
                    self.project_id,
                    DocumentKind.COURSE_MATERIAL,
                    filename="second.pdf",
                    content_type="application/pdf",
                    size_bytes=100,
                    upload_endpoint="http://local.invalid/{document_id}",
                )
            self._finish_without_processing(db, accepted)

            for _ in range(3):
                service, document = self._upload(db, self.workspace_id, self.project_id, 300)
                accepted = service.confirm_upload(self.workspace_id, self.project_id, document.id)
                self._finish_without_processing(db, accepted)

            service, rejected = self._upload(db, self.workspace_id, self.project_id, 1)
            rejected_key = rejected.storage_key
            with self.assertRaisesRegex(DocumentQuotaError, "最近24小时"):
                service.confirm_upload(self.workspace_id, self.project_id, rejected.id)
            self.assertFalse(self.storage.object_exists(rejected_key or ""))
            self.assertEqual(db.get(Document, rejected.id).quota_pages, 0)  # type: ignore[union-attr]

    def test_single_600_is_accepted_and_601_is_deleted(self) -> None:
        with self.Session() as db:
            service, document = self._upload(db, self.workspace_id, self.project_id, 600)
            self.assertEqual(service.confirm_upload(self.workspace_id, self.project_id, document.id).total_pages, 600)
            self._finish_without_processing(db, document)

            service, too_large = self._upload(db, self.workspace_id, self.project_id, 601)
            key = too_large.storage_key
            with self.assertRaisesRegex(DocumentProcessingError, "600"):
                service.confirm_upload(self.workspace_id, self.project_id, too_large.id)
            self.assertFalse(self.storage.object_exists(key or ""))

    def test_global_rolling_limit_and_fifo_single_processing_claim(self) -> None:
        second_workspace, second_project = self._workspace_project("队列二")
        with self.Session() as db:
            now = datetime.now(UTC)
            for index in range(5):
                workspace_id, project_id = self._workspace_project(f"历史 {index}")
                db.add(Document(
                    project_id=project_id,
                    kind=DocumentKind.COURSE_MATERIAL,
                    original_name="history.pdf",
                    mime_type="application/pdf",
                    size_bytes=1,
                    storage_backend="local",
                    processing_status=DocumentProcessingStatus.PARSED,
                    processing_phase="completed",
                    total_pages=600,
                    quota_pages=600,
                    accepted_at=now,
                    queued_at=now,
                ))
            db.commit()
            service, rejected = self._upload(db, self.workspace_id, self.project_id, 1)
            with self.assertRaisesRegex(DocumentQuotaError, "全站"):
                service.confirm_upload(self.workspace_id, self.project_id, rejected.id)

            # Released quota rows do not count, so the queue can be tested in the same database.
            for old in db.scalars(select(Document).where(Document.quota_pages == 600)).all():
                old.quota_released_at = now
            db.commit()
            first_service, first = self._upload(db, self.workspace_id, self.project_id, 2)
            first_service.confirm_upload(self.workspace_id, self.project_id, first.id)
            second_service, second = self._upload(db, second_workspace, second_project, 2)
            second_service.confirm_upload(second_workspace, second_project, second.id)
            claimed = first_service.claim_next("worker-one")
            self.assertEqual(claimed, first.id)
            self.assertIsNone(second_service.claim_next("worker-two"))
            first.processing_status = DocumentProcessingStatus.PARSED
            first.lease_owner = None
            first.lease_expires_at = None
            db.commit()
            self.assertEqual(second_service.claim_next("worker-two"), second.id)

    def test_queue_runner_automatically_processes_next_document(self) -> None:
        second_workspace, second_project = self._workspace_project("自动队列二")
        with self.Session() as db:
            service, first = self._upload(db, self.workspace_id, self.project_id, 2)
            service.confirm_upload(self.workspace_id, self.project_id, first.id)
            service, second = self._upload(db, second_workspace, second_project, 2)
            service.confirm_upload(second_workspace, second_project, second.id)
            completed = service.resume_incomplete()
            self.assertEqual(completed, [first.id, second.id])
            self.assertEqual(db.get(Document, first.id).processing_status, DocumentProcessingStatus.PARSED)  # type: ignore[union-attr]
            self.assertEqual(db.get(Document, second.id).processing_status, DocumentProcessingStatus.PARSED)  # type: ignore[union-attr]

    def test_object_scope_cannot_cross_workspace(self) -> None:
        document_id = uuid.uuid4()
        key = build_object_key(self.workspace_id, document_id)
        validate_object_scope(key, self.workspace_id, document_id)
        with self.assertRaises(UploadAuthorizationError):
            validate_object_scope(key, uuid.uuid4(), document_id)

    def test_unstartable_internal_failure_releases_quota_once(self) -> None:
        with self.Session() as db:
            service, document = self._upload(db, self.workspace_id, self.project_id, 2)
            service.confirm_upload(self.workspace_id, self.project_id, document.id)
            assert document.storage_key is not None
            self.storage.delete_object(document.storage_key)
            with self.assertRaises(DocumentProcessingError):
                service.process_document(document.id)
            failed = db.get(Document, document.id)
            assert failed is not None
            released_at = failed.quota_released_at
            self.assertIsNotNone(released_at)
            self.assertFalse(service._release_quota_once(failed))
            db.commit()
            self.assertEqual(db.get(Document, document.id).quota_released_at, released_at)  # type: ignore[union-attr]

    def test_concurrent_acceptance_cannot_bypass_global_page_limit(self) -> None:
        database_path = Path(self.temp.name) / "concurrent.db"
        engine = create_engine(
            f"sqlite+pysqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        Base.metadata.create_all(engine)
        identities: list[tuple[uuid.UUID, uuid.UUID]] = []
        with Session() as db:
            for index in range(2):
                workspace_id, project_id = uuid.uuid4(), uuid.uuid4()
                db.add(Workspace(id=workspace_id))
                db.add(Project(id=project_id, workspace_id=workspace_id, name=f"并发 {index}"))
                identities.append((workspace_id, project_id))
            db.commit()

        data = make_pdf(600)
        document_ids: list[uuid.UUID] = []
        for workspace_id, project_id in identities:
            with Session() as db:
                service = self._service(db, workspace_pages=1200, global_pages=600)
                document, _ = service.create_upload(
                    workspace_id,
                    project_id,
                    DocumentKind.COURSE_MATERIAL,
                    filename="600.pdf",
                    content_type="application/pdf",
                    size_bytes=len(data),
                    upload_endpoint="http://local.invalid/{document_id}",
                )
                assert document.storage_key is not None
                path = self.storage.resolve(document.storage_key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                document_ids.append(document.id)

        def confirm(index: int) -> str:
            workspace_id, project_id = identities[index]
            with Session() as db:
                service = self._service(db, workspace_pages=1200, global_pages=600)
                try:
                    service.confirm_upload(workspace_id, project_id, document_ids[index])
                    return "accepted"
                except DocumentQuotaError:
                    return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, range(2)))
        self.assertEqual(sorted(results), ["accepted", "rejected"])
        with Session() as db:
            documents = list(db.scalars(select(Document).where(Document.id.in_(document_ids))).all())
            self.assertEqual(sum(document.quota_pages for document in documents), 600)
            rejected = next(document for document in documents if document.quota_pages == 0)
            self.assertIsNone(rejected.storage_key)
        engine.dispose()


class PublicWorkspaceIsolationAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(_env_file=None, public_access_enabled=True)
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

        def override_db():
            with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def test_two_public_workspaces_are_isolated_and_unauthorized_is_401(self) -> None:
        first = self.client.post("/api/v1/auth/anonymous").json()
        second = self.client.post("/api/v1/auth/anonymous").json()
        signer = SessionTokenSigner(self.settings.session_signing_key)
        first_headers = {"Authorization": f"Bearer {signer.issue(uuid.UUID(first['workspace_id']), 'public')}"}
        second_headers = {"Authorization": f"Bearer {signer.issue(uuid.UUID(second['workspace_id']), 'public')}"}
        project = self.client.post("/api/v1/projects", headers=first_headers, json={"name": "私有项目"}).json()
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/v1/projects").status_code, 401)
        self.assertEqual(self.client.get(f"/api/v1/projects/{project['id']}", headers=second_headers).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/projects", headers=second_headers).json(), [])


if __name__ == "__main__":
    unittest.main()
