import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app


class AccessControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(_env_file=None, app_access_code="test-access-code")
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

    def test_access_code_issues_signed_workspace_session(self) -> None:
        rejected = self.client.post("/api/v1/auth/access", json={"access_code": "wrong"})
        self.assertEqual(rejected.status_code, 401)

        issued = self.client.post("/api/v1/auth/access", json={"access_code": "test-access-code"})
        self.assertEqual(issued.status_code, 200, issued.text)
        token = issued.json()["token"]
        workspace_id = issued.json()["workspace_id"]
        verified = self.client.get(
            "/api/v1/auth/session",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(verified.json(), {"workspace_id": workspace_id})

        tampered = self.client.get(
            "/api/v1/auth/session",
            headers={"Authorization": f"Bearer {token[:-1]}x"},
        )
        self.assertEqual(tampered.status_code, 401)


class ProductionConfigurationTests(unittest.TestCase):
    def test_neon_urls_and_both_cors_formats_are_supported(self) -> None:
        postgres = Settings(_env_file=None, database_url="postgresql://user:pass@host/db")
        self.assertTrue(postgres.database_url.startswith("postgresql+psycopg://"))
        legacy = Settings(_env_file=None, database_url="postgres://user:pass@host/db")
        self.assertTrue(legacy.database_url.startswith("postgresql+psycopg://"))
        self.assertEqual(
            Settings(_env_file=None, cors_origins="https://one.vercel.app, https://two.vercel.app").cors_origins,
            ["https://one.vercel.app", "https://two.vercel.app"],
        )
        self.assertEqual(
            Settings(_env_file=None, cors_origins='["https://one.vercel.app"]').cors_origins,
            ["https://one.vercel.app"],
        )

    def test_production_rejects_local_or_default_secrets(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, environment="production")


class AlembicMigrationTests(unittest.TestCase):
    def test_empty_database_upgrades_twice_and_matches_metadata(self) -> None:
        backend_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
            environment = {**os.environ, "DATABASE_URL": database_url}
            for command in ("upgrade", "upgrade", "check"):
                arguments = [sys.executable, "-m", "alembic"]
                arguments += ["upgrade", "head"] if command == "upgrade" else ["check"]
                completed = subprocess.run(
                    arguments,
                    cwd=backend_root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            engine = create_engine(database_url)
            with engine.connect() as connection:
                revision = connection.exec_driver_sql("select version_num from alembic_version").scalar_one()
                table_names = set(connection.dialect.get_table_names(connection))
            self.assertEqual(revision, "20260715_0001")
            self.assertEqual(table_names - {"alembic_version"}, set(Base.metadata.tables))
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
