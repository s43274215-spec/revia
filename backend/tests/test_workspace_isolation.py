import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.content import BulletPoint, Chapter, KnowledgePoint
from app.models.project import Project
from app.models.workspace import Workspace
from tests.helpers import authorization_header
from tests.test_document_processing import build_test_pdf


class WorkspaceIsolationAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.owner_id = uuid.uuid4()
        self.other_id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.bullet_id = uuid.uuid4()
        with self.Session() as session:
            session.add_all([Workspace(id=self.owner_id), Workspace(id=self.other_id)])
            project = Project(id=self.project_id, workspace_id=self.owner_id, name="私有课程")
            chapter = Chapter(title="私有章节", position=0)
            point = KnowledgePoint(title="私有知识点", position=0)
            point.bullet_points.append(BulletPoint(id=self.bullet_id, position=0))
            chapter.knowledge_points.append(point)
            project.chapters.append(chapter)
            session.add(project)
            session.commit()

        def override_db():
            with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            database_url="sqlite+pysqlite:///:memory:",
            ai_mode="mock",
            public_access_enabled=True,
        )
        self.client = TestClient(app)
        self.other_headers = authorization_header(self.other_id)

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def test_other_workspace_cannot_reach_project_or_descendants(self) -> None:
        self.assertEqual(
            self.client.get(f"/api/v1/projects/{self.project_id}", headers=self.other_headers).status_code,
            404,
        )
        upload = self.client.post(
            f"/api/v1/projects/{self.project_id}/documents",
            headers=self.other_headers,
            data={"kind": "course_material"},
            files={"file": ("private.pdf", build_test_pdf(), "application/pdf")},
        )
        self.assertEqual(upload.status_code, 404)
        direct_upload = self.client.post(
            f"/api/v1/projects/{self.project_id}/documents/uploads",
            headers=self.other_headers,
            json={
                "kind": "course_material",
                "filename": "private.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(build_test_pdf()),
            },
        )
        self.assertEqual(direct_upload.status_code, 404)
        syllabus = self.client.put(
            f"/api/v1/projects/{self.project_id}/syllabus",
            headers=self.other_headers,
            json={"text": "私有考纲"},
        )
        self.assertEqual(syllabus.status_code, 404)
        generation = self.client.post(
            f"/api/v1/projects/{self.project_id}/generation-jobs",
            headers=self.other_headers,
        )
        self.assertEqual(generation.status_code, 404)
        material = self.client.get(
            f"/api/v1/projects/{self.project_id}/learning-material",
            headers=self.other_headers,
        )
        self.assertEqual(material.status_code, 404)
        bullet = self.client.delete(
            f"/api/v1/bullet-points/{self.bullet_id}",
            headers=self.other_headers,
        )
        self.assertEqual(bullet.status_code, 404)


if __name__ == "__main__":
    unittest.main()
