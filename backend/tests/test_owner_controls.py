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
from app.auth.security import SessionTokenSigner
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.document.parser import PDFParser
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.main import app
from app.models.enums import DocumentKind, DocumentProcessingStatus, WorkspaceRole
from app.models.project import Document, Project
from app.models.workspace import SiteSettings, Workspace
from app.services.document_processing import DocumentProcessingError, DocumentProcessingService, DocumentQuotaError
from app.services.storage import LocalStorageProvider
from tests.test_public_beta_storage import make_pdf


class OwnerAccessControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = uuid.uuid4()
        self.demo_id = uuid.uuid4()
        self.settings = Settings(
            _env_file=None,
            app_access_code="owner-access-code",
            owner_workspace_id=self.owner_id,
            demo_access_code="demo-access-code",
            demo_workspace_id=self.demo_id,
            public_access_enabled=True,
        )
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        with self.Session() as db:
            db.add_all([
                Workspace(id=self.owner_id, role=WorkspaceRole.OWNER, owner_slot=1),
                Workspace(id=self.demo_id, role=WorkspaceRole.PUBLIC),
            ])
            db.commit()

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

    def _owner_session(self) -> dict[str, str]:
        response = self.client.post("/api/v1/auth/access", json={"access_code": "owner-access-code"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _headers(self, session: dict[str, str]) -> dict[str, str]:
        token = SessionTokenSigner(self.settings.session_signing_key).issue(
            uuid.UUID(session["workspace_id"]),
            session["role"],
        )
        return {"Authorization": f"Bearer {token}"}

    def test_owner_login_is_stable_unique_and_role_cannot_be_forged(self) -> None:
        self.assertEqual(
            self.client.post("/api/v1/auth/access", json={"access_code": "wrong"}).json()["detail"],
            "访问码无效",
        )
        first = self._owner_session()
        second = self._owner_session()
        self.assertEqual(first["workspace_id"], second["workspace_id"])
        self.assertEqual(first["workspace_id"], str(self.owner_id))
        self.assertEqual(first["role"], "owner")
        self.assertNotIn("token", first)
        verified = self.client.get("/api/v1/auth/session", headers=self._headers(first))
        self.assertEqual(verified.json()["role"], "owner")
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count(Workspace.id)).where(Workspace.role == WorkspaceRole.OWNER)), 1)
            public = Workspace(role=WorkspaceRole.PUBLIC)
            db.add(public)
            db.commit()
            forged = SessionTokenSigner(self.settings.session_signing_key).issue(public.id, WorkspaceRole.OWNER)
        rejected = self.client.get(
            "/api/v1/auth/session",
            headers={"Authorization": f"Bearer {forged}"},
        )
        self.assertEqual(rejected.status_code, 401)

    def test_demo_cookie_is_http_only_and_workspace_is_read_only(self) -> None:
        owner = self._owner_session()
        owner_project = self.client.post(
            "/api/v1/projects",
            headers=self._headers(owner),
            json={"name": "站长私有项目"},
        ).json()
        response = self.client.post("/api/v1/auth/access", json={"access_code": "demo-access-code"})
        self.assertEqual(response.status_code, 200, response.text)
        demo = response.json()
        self.assertEqual(demo["workspace_id"], str(self.demo_id))
        self.assertEqual(demo["role"], "demo")
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        headers = self._headers(demo)
        self.assertEqual(self.client.get("/api/v1/projects", headers=headers).json(), [])
        self.assertEqual(
            self.client.get(f"/api/v1/projects/{owner_project['id']}", headers=headers).status_code,
            404,
        )
        blocked = self.client.post("/api/v1/projects", headers=headers, json={"name": "禁止创建"})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["detail"], "演示模式不会保存修改")
        logout = self.client.post("/api/v1/auth/logout", headers=headers)
        self.assertEqual(logout.status_code, 204)

    def test_runtime_switch_persists_blocks_public_and_keeps_owner_available(self) -> None:
        public = self.client.post("/api/v1/auth/anonymous").json()
        public_headers = self._headers(public)
        project = self.client.post("/api/v1/projects", headers=public_headers, json={"name": "保留项目"})
        self.assertEqual(project.status_code, 201)
        processing_document_id = uuid.uuid4()
        with self.Session() as db:
            db.add(Document(
                id=processing_document_id,
                project_id=uuid.UUID(project.json()["id"]),
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="processing.pdf",
                mime_type="application/pdf",
                size_bytes=1,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.PROCESSING,
                processing_phase="extracting",
            ))
            db.commit()
        owner = self._owner_session()
        owner_headers = self._headers(owner)

        self.assertEqual(self.client.get("/api/v1/settings/site", headers=public_headers).status_code, 403)
        closed = self.client.put(
            "/api/v1/settings/site",
            headers=owner_headers,
            json={"public_access_enabled": False},
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertFalse(closed.json()["public_access_enabled"])
        self.assertEqual(self.client.get("/api/v1/auth/mode").json()["public_access_enabled"], False)
        self.assertEqual(self.client.post("/api/v1/auth/anonymous").status_code, 403)
        blocked = self.client.get("/api/v1/projects", headers=public_headers)
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["detail"], "Revia 当前暂未开放，请稍后再试。")
        upload_blocked = self.client.post(
            f"/api/v1/projects/{project.json()['id']}/documents/uploads",
            headers=public_headers,
            json={
                "kind": "course_material",
                "filename": "blocked.pdf",
                "content_type": "application/pdf",
                "size_bytes": 100,
            },
        )
        self.assertEqual(upload_blocked.status_code, 403)
        generation_blocked = self.client.post(
            f"/api/v1/projects/{project.json()['id']}/generation-jobs",
            headers=public_headers,
        )
        self.assertEqual(generation_blocked.status_code, 403)
        owner_project = self.client.post("/api/v1/projects", headers=owner_headers, json={"name": "站长项目"})
        self.assertEqual(owner_project.status_code, 201, owner_project.text)

        with self.Session() as db:
            stored = db.get(SiteSettings, 1)
            assert stored is not None
            self.assertFalse(stored.public_access_enabled)
            self.assertEqual(str(stored.updated_by_workspace_id), owner["workspace_id"])
            self.assertEqual(
                db.get(Document, processing_document_id).processing_status,  # type: ignore[union-attr]
                DocumentProcessingStatus.PROCESSING,
            )

        reopened = self.client.put(
            "/api/v1/settings/site",
            headers=owner_headers,
            json={"public_access_enabled": True},
        )
        self.assertTrue(reopened.json()["public_access_enabled"])
        restored = self.client.get(f"/api/v1/projects/{project.json()['id']}", headers=public_headers)
        self.assertEqual(restored.status_code, 200)


class OwnerQuotaAndQueueTests(unittest.TestCase):
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
        self.owner_id, self.owner_project_id = self._workspace_project("站长", WorkspaceRole.OWNER, owner_slot=1)
        self.public_id, self.public_project_id = self._workspace_project("普通", WorkspaceRole.PUBLIC)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def _workspace_project(
        self,
        name: str,
        role: WorkspaceRole,
        *,
        owner_slot: int | None = None,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        workspace_id, project_id = uuid.uuid4(), uuid.uuid4()
        with self.Session() as db:
            db.add(Workspace(id=workspace_id, role=role, owner_slot=owner_slot))
            db.add(Project(id=project_id, workspace_id=workspace_id, name=name))
            db.commit()
        return workspace_id, project_id

    def _service(self, db) -> DocumentProcessingService:
        return DocumentProcessingService(
            db,
            self.storage,
            PDFParser(max_pages=600),
            TextStructurer(),
            StructuredTextSplitter(),
            workspace_rolling_24h_page_limit=1,
            global_rolling_24h_page_limit=1,
            workspace_max_active_documents=1,
            global_max_processing_documents=1,
        )

    def _upload(
        self,
        db,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        pages: int,
    ) -> tuple[DocumentProcessingService, Document]:
        data = make_pdf(pages)
        service = self._service(db)
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

    def test_owner_processes_600_pages_bypasses_page_quotas_but_keeps_safety_limits(self) -> None:
        with self.Session() as db:
            service, document = self._upload(db, self.owner_id, self.owner_project_id, 600)
            accepted = service.confirm_upload(self.owner_id, self.owner_project_id, document.id)
            self.assertEqual(accepted.total_pages, 600)
            self.assertEqual(accepted.quota_pages, 600)
            self.assertEqual(accepted.queue_priority, 0)
            with self.assertRaisesRegex(DocumentQuotaError, "正在排队或处理中"):
                service.create_upload(
                    self.owner_id,
                    self.owner_project_id,
                    DocumentKind.COURSE_MATERIAL,
                    filename="second.pdf",
                    content_type="application/pdf",
                    size_bytes=100,
                    upload_endpoint="http://local.invalid/{document_id}",
                )
            parsed = service.process_document(document.id)
            self.assertEqual(parsed.processed_pages, 600)
            self.assertEqual(parsed.processing_status, DocumentProcessingStatus.PARSED)

            with self.assertRaisesRegex(DocumentProcessingError, "150MB"):
                service.create_upload(
                    self.owner_id,
                    self.owner_project_id,
                    DocumentKind.COURSE_MATERIAL,
                    filename="oversize.pdf",
                    content_type="application/pdf",
                    size_bytes=151 * 1024 * 1024,
                    upload_endpoint="http://local.invalid/{document_id}",
                )
            service, too_long = self._upload(db, self.owner_id, self.owner_project_id, 601)
            with self.assertRaisesRegex(DocumentProcessingError, "600"):
                service.confirm_upload(self.owner_id, self.owner_project_id, too_long.id)

    def test_processing_is_not_preempted_then_owner_is_claimed_before_public_fifo(self) -> None:
        now = datetime.now(UTC)
        with self.Session() as db:
            processing_public = Document(
                project_id=self.public_project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="processing.pdf",
                mime_type="application/pdf",
                size_bytes=1,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.PROCESSING,
                processing_phase="extracting",
                accepted_at=now - timedelta(minutes=3),
                queued_at=now - timedelta(minutes=3),
                queue_priority=10,
                lease_owner="active-worker",
                lease_expires_at=now + timedelta(minutes=5),
            )
            public_first = Document(
                project_id=self.public_project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="public-first.pdf",
                mime_type="application/pdf",
                size_bytes=1,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.QUEUED,
                processing_phase="queued",
                accepted_at=now - timedelta(minutes=2),
                queued_at=now - timedelta(minutes=2),
                queue_priority=10,
            )
            owner_waiting = Document(
                project_id=self.owner_project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="owner.pdf",
                mime_type="application/pdf",
                size_bytes=1,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.QUEUED,
                processing_phase="queued",
                accepted_at=now - timedelta(minutes=1),
                queued_at=now - timedelta(minutes=1),
                queue_priority=0,
            )
            public_second = Document(
                project_id=self.public_project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="public-second.pdf",
                mime_type="application/pdf",
                size_bytes=1,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.QUEUED,
                processing_phase="queued",
                accepted_at=now,
                queued_at=now,
                queue_priority=10,
            )
            db.add_all([processing_public, public_first, owner_waiting, public_second])
            db.commit()
            service = self._service(db)
            self.assertIsNone(service.claim_next("new-worker"))
            self.assertEqual(db.get(Document, processing_public.id).lease_owner, "active-worker")  # type: ignore[union-attr]

            processing_public.processing_status = DocumentProcessingStatus.PARSED
            processing_public.lease_owner = None
            processing_public.lease_expires_at = None
            db.commit()
            owner_waiting_id = owner_waiting.id
            public_first_id = public_first.id

        with self.Session() as restarted_db:
            restarted_service = self._service(restarted_db)
            self.assertEqual(restarted_service.claim_next("owner-worker"), owner_waiting_id)
            stored_owner = restarted_db.get(Document, owner_waiting_id)
            assert stored_owner is not None
            stored_owner.processing_status = DocumentProcessingStatus.PARSED
            stored_owner.lease_owner = None
            stored_owner.lease_expires_at = None
            restarted_db.commit()

        with self.Session() as next_db:
            self.assertEqual(self._service(next_db).claim_next("public-worker"), public_first_id)


if __name__ == "__main__":
    unittest.main()
