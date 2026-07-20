import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.core.config import Settings, get_settings
from app.main import app
from app.models.enums import DocumentKind, DocumentProcessingStatus, GenerationStatus, ProjectStatus
from app.models.project import Document, GenerationJob, Project
from app.models.workspace import Workspace
from tests.helpers import authorization_header


class ProjectAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.workspace_id = uuid.uuid4()
        with self.Session() as session:
            session.add(Workspace(id=self.workspace_id))
            session.commit()

        def override_db():
            with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            public_access_enabled=True,
            file_storage_root=self.temp.name,
        )
        self.client = TestClient(app)
        self.client.headers.update(authorization_header(self.workspace_id))

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp.cleanup()

    def test_project_crud_uses_persisted_uuid(self) -> None:
        created = self.client.post("/api/v1/projects", json={"name": "测试课程", "description": "真实项目"})
        self.assertEqual(created.status_code, 201, created.text)
        project = created.json()
        self.assertEqual(project["status"], "not_uploaded")

        listed = self.client.get("/api/v1/projects")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["id"], project["id"])

        fetched = self.client.get(f"/api/v1/projects/{project['id']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["name"], "测试课程")

        updated = self.client.patch(f"/api/v1/projects/{project['id']}", json={"name": "更新课程"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["name"], "更新课程")

        deleted = self.client.delete(f"/api/v1/projects/{project['id']}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(f"/api/v1/projects/{project['id']}").status_code, 404)

    def test_unauthorized_requests_are_rejected(self) -> None:
        response = self.client.get("/api/v1/projects", headers={"Authorization": ""})
        self.assertEqual(response.status_code, 401)

    def test_other_workspace_cannot_read_project(self) -> None:
        created = self.client.post("/api/v1/projects", json={"name": "隔离课程"})
        other_workspace_id = uuid.uuid4()
        with self.Session() as session:
            session.add(Workspace(id=other_workspace_id))
            session.commit()
        response = self.client.get(
            f"/api/v1/projects/{created.json()['id']}",
            headers=authorization_header(other_workspace_id),
        )
        self.assertEqual(response.status_code, 404)

    def test_active_document_is_workspace_scoped_and_returns_progress(self) -> None:
        other_workspace_id = uuid.uuid4()
        project_id = uuid.uuid4()
        other_project_id = uuid.uuid4()
        with self.Session() as session:
            session.add(Workspace(id=other_workspace_id))
            session.add(Project(id=project_id, workspace_id=self.workspace_id, name="人力资源"))
            session.add(Project(id=other_project_id, workspace_id=other_workspace_id, name="其他工作区"))
            session.flush()
            session.add(Document(
                project_id=project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="1.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
                storage_backend="s3",
                processing_status=DocumentProcessingStatus.INTERRUPTED,
                processing_phase="resource_limited",
                current_page=57,
                total_pages=100,
                processed_pages=57,
                error_message="OCR 处理因服务器资源不足暂停，系统稍后可从当前页继续。",
            ))
            session.add(Document(
                project_id=other_project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="private.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
                storage_backend="s3",
                processing_status=DocumentProcessingStatus.PROCESSING,
                processing_phase="extracting",
            ))
            session.commit()

        response = self.client.get("/api/v1/projects/active-document")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["project_id"], str(project_id))
        self.assertEqual(payload["filename"], "1.pdf")
        self.assertEqual(payload["project_name"], "人力资源")
        self.assertEqual(payload["processing_status"], "interrupted")
        self.assertEqual(payload["processing_phase"], "resource_limited")
        self.assertEqual(payload["current_page"], 57)
        self.assertEqual(payload["processed_pages"], 57)
        self.assertEqual(payload["total_pages"], 100)
        self.assertIn("资源不足暂停", payload["error_message"])

        isolated = self.client.get(
            "/api/v1/projects/active-document",
            headers=authorization_header(other_workspace_id),
        )
        self.assertEqual(isolated.status_code, 200, isolated.text)
        self.assertEqual(isolated.json()["filename"], "private.pdf")


    def test_delete_processing_project_stops_tasks_removes_storage_and_cascades(self) -> None:
        project_id = uuid.uuid4()
        document_id = uuid.uuid4()
        storage_key = f"workspaces/{self.workspace_id}/documents/{document_id}.pdf"
        stored_file = Path(self.temp.name) / storage_key
        stored_file.parent.mkdir(parents=True, exist_ok=True)
        stored_file.write_bytes(b"pdf")
        with self.Session() as session:
            project = Project(
                id=project_id,
                workspace_id=self.workspace_id,
                name="处理中项目",
                status=ProjectStatus.PROCESSING,
            )
            project.documents.append(Document(
                id=document_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="processing.pdf",
                mime_type="application/pdf",
                size_bytes=3,
                storage_key=storage_key,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.PROCESSING,
                processing_phase="extracting",
                lease_owner="worker",
            ))
            project.generation_jobs.append(GenerationJob(
                status=GenerationStatus.GENERATING,
                progress=30,
                total_items=8,
            ))
            session.add(project)
            session.commit()

        response = self.client.delete(f"/api/v1/projects/{project_id}")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertFalse(stored_file.exists())
        with self.Session() as session:
            self.assertIsNone(session.get(Project, project_id))
            self.assertIsNone(session.get(Document, document_id))
            self.assertEqual(session.query(GenerationJob).filter_by(project_id=project_id).count(), 0)

    def test_storage_cleanup_failure_is_reported_and_active_work_is_stopped(self) -> None:
        project_id = uuid.uuid4()
        document_id = uuid.uuid4()
        storage_key = f"workspaces/{self.workspace_id}/documents/{document_id}.pdf"
        with self.Session() as session:
            project = Project(
                id=project_id,
                workspace_id=self.workspace_id,
                name="删除失败项目",
                status=ProjectStatus.PROCESSING,
            )
            project.documents.append(Document(
                id=document_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="cannot-delete.pdf",
                mime_type="application/pdf",
                size_bytes=3,
                storage_key=storage_key,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.PROCESSING,
                processing_phase="extracting",
                lease_owner="worker",
            ))
            project.generation_jobs.append(GenerationJob(
                status=GenerationStatus.GENERATING,
                progress=30,
                total_items=8,
            ))
            session.add(project)
            session.commit()

        class FailingStorage:
            def delete_object(self, object_key: str) -> None:
                raise RuntimeError(object_key)

        with patch("app.api.v1.endpoints.projects.build_storage_provider", return_value=FailingStorage()):
            response = self.client.delete(f"/api/v1/projects/{project_id}")

        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("cannot-delete.pdf", response.json()["detail"])
        with self.Session() as session:
            project = session.get(Project, project_id)
            document = session.get(Document, document_id)
            job = session.query(GenerationJob).filter_by(project_id=project_id).one()
            self.assertIsNotNone(project)
            self.assertEqual(document.processing_status, DocumentProcessingStatus.CANCELLED)
            self.assertIsNone(document.lease_owner)
            self.assertEqual(job.status, GenerationStatus.FAILED)
            self.assertIsNotNone(job.completed_at)


if __name__ == "__main__":
    unittest.main()
