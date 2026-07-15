import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.core.config import Settings, get_settings
from app.main import app
from app.models.workspace import Workspace
from tests.helpers import authorization_header


class ProjectAPITests(unittest.TestCase):
    def setUp(self) -> None:
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
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, public_access_enabled=True)
        self.client = TestClient(app)
        self.client.headers.update(authorization_header(self.workspace_id))

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

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


if __name__ == "__main__":
    unittest.main()
