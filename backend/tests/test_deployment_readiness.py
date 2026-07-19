import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.auth.security import SessionTokenSigner
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.workspace import SiteSettings
from app.services.storage import build_object_key, build_storage_provider


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
        workspace_id = issued.json()["workspace_id"]
        self.assertNotIn("token", issued.json())
        self.assertIn("httponly", issued.headers["set-cookie"].lower())
        verified = self.client.get("/api/v1/auth/session")
        self.assertEqual(verified.json(), {"workspace_id": workspace_id, "role": "owner"})
        repeated = self.client.post("/api/v1/auth/access", json={"access_code": "test-access-code"})
        self.assertEqual(repeated.json()["workspace_id"], workspace_id)

        tampered = self.client.get(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer invalid.tampered"},
        )
        self.assertEqual(tampered.status_code, 401)

    def test_public_mode_issues_anonymous_workspace_and_private_mode_hides_endpoint(self) -> None:
        mode = self.client.get("/api/v1/auth/mode")
        self.assertEqual(mode.json(), {"public_access_enabled": False, "demo_access_enabled": False})
        self.assertEqual(self.client.post("/api/v1/auth/anonymous").status_code, 403)

        with self.Session() as session:
            session.execute(delete(SiteSettings))
            session.commit()
        public_settings = Settings(_env_file=None, public_access_enabled=True)
        app.dependency_overrides[get_settings] = lambda: public_settings
        self.assertEqual(
            self.client.get("/api/v1/auth/mode").json(),
            {"public_access_enabled": True, "demo_access_enabled": False},
        )
        first = self.client.post("/api/v1/auth/anonymous")
        second = self.client.post("/api/v1/auth/anonymous")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertNotEqual(first.json()["workspace_id"], second.json()["workspace_id"])
        first_token = SessionTokenSigner(public_settings.session_signing_key).issue(
            uuid.UUID(first.json()["workspace_id"]),
            "public",
        )
        session = self.client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {first_token}"})
        self.assertEqual(session.status_code, 200)


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

    def test_production_requires_s3_and_accepts_complete_private_storage_configuration(self) -> None:
        common = {
            "environment": "production",
            "database_url": "postgresql://user:pass@neon.example/revia",
            "cors_origins": ["https://revia.vercel.app"],
            "app_access_code": "private-access",
            "session_signing_key": "production-signing-key-with-more-than-32-bytes",
            "credential_encryption_key": Fernet.generate_key().decode(),
            "ai_mode": "live",
            "owner_workspace_id": uuid.uuid4(),
            "demo_access_code": "private-demo-access",
            "demo_workspace_id": uuid.uuid4(),
        }
        with self.assertRaisesRegex(ValidationError, "STORAGE_BACKEND"):
            Settings(_env_file=None, **common)
        settings = Settings(
            _env_file=None,
            **common,
            storage_backend="s3",
            s3_region="auto",
            s3_access_key_id="access-id",
            s3_secret_access_key="synthetic-secret",
            s3_bucket_name="private-revia",
            s3_endpoint="https://s3.example.com",
        )
        self.assertEqual(settings.storage_backend, "s3")
        provider = build_storage_provider(settings)
        workspace_id = uuid.uuid4()
        document_id = uuid.uuid4()
        target = provider.create_upload_url(
            build_object_key(workspace_id, document_id),
            workspace_id=workspace_id,
            document_id=document_id,
            content_type="application/pdf",
            expires_in=60,
        )
        self.assertIn("X-Amz-Signature", target.url)
        self.assertIn("X-Amz-Expires=60", target.url)


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
            try:
                with engine.connect() as connection:
                    revision = connection.exec_driver_sql("select version_num from alembic_version").scalar_one()
                    table_names = set(connection.dialect.get_table_names(connection))
                self.assertEqual(revision, "20260718_0007")
                self.assertEqual(table_names - {"alembic_version"}, set(Base.metadata.tables))
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
