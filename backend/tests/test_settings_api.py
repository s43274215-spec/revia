import base64
import unittest
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.ai.clients.base import AIClient, AIRequestError
from app.api.v1.endpoints.settings import get_deepseek_settings_service
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.workspace import DeepSeekCredential, Workspace
from app.settings.security import CredentialCipher, TransportKeyPair
from app.settings.service import DeepSeekSettingsService
from tests.helpers import authorization_header


class SuccessfulClient(AIClient):
    async def generate_completion(self, *, system_prompt: str, user_prompt: str) -> str:
        return '{"ok": true}'


class RejectedClient(AIClient):
    async def generate_completion(self, *, system_prompt: str, user_prompt: str) -> str:
        raise AIRequestError("AI provider request failed with status 401")


def encrypt_for_transport(public_key_pem: str, secret: str) -> str:
    public_key = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    encrypted = public_key.encrypt(
        secret.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(encrypted).decode("ascii")


class DeepSeekSettingsAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(_env_file=None, public_access_enabled=True)
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
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.transport = TransportKeyPair()
        self.client = TestClient(app)
        self.client.headers.update(authorization_header(self.workspace_id, self.settings))

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def test_status_save_and_restart_round_trip_never_exposes_plaintext(self) -> None:
        status = self.client.get("/api/v1/settings/deepseek")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json(), {"configured": False, "masked_hint": None})

        public_key_response = self.client.get("/api/v1/settings/deepseek/encryption-key")
        self.assertEqual(public_key_response.status_code, 200)
        api_key = "sk-private-browser-test-123456"
        encrypted = encrypt_for_transport(public_key_response.json()["public_key"], api_key)
        request_body = {"encrypted_api_key": encrypted}
        self.assertNotIn(api_key, str(request_body))

        saved = self.client.put("/api/v1/settings/deepseek", json=request_body)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["configured"])
        self.assertEqual(saved.json()["masked_hint"], "••••3456")
        self.assertNotIn(api_key, saved.text)

        with self.Session() as session:
            credential = session.scalar(select(DeepSeekCredential).where(
                DeepSeekCredential.workspace_id == self.workspace_id
            ))
            self.assertIsNotNone(credential)
            assert credential is not None
            self.assertNotIn(api_key.encode("utf-8"), credential.encrypted_secret)

        with self.Session() as restarted_session:
            restarted_service = DeepSeekSettingsService(
                db=restarted_session,
                workspace_id=self.workspace_id,
                settings=self.settings,
                cipher=CredentialCipher(self.settings.credential_encryption_key),
                transport=TransportKeyPair(),
            )
            self.assertEqual(restarted_service.read_api_key(), api_key)

        refreshed = self.client.get("/api/v1/settings/deepseek")
        self.assertEqual(refreshed.json(), {"configured": True, "masked_hint": "••••3456"})

    def test_credentials_are_isolated_by_workspace(self) -> None:
        api_key = "sk-workspace-isolation-654321"
        public_key = self.client.get("/api/v1/settings/deepseek/encryption-key").json()["public_key"]
        self.client.put(
            "/api/v1/settings/deepseek",
            json={"encrypted_api_key": encrypt_for_transport(public_key, api_key)},
        )
        other_workspace_id = uuid.uuid4()
        with self.Session() as session:
            session.add(Workspace(id=other_workspace_id))
            session.commit()
        response = self.client.get(
            "/api/v1/settings/deepseek",
            headers=authorization_header(other_workspace_id, self.settings),
        )
        self.assertEqual(response.json(), {"configured": False, "masked_hint": None})

    def test_connection_returns_clear_success_and_failure_messages(self) -> None:
        api_key = "sk-connection-test-123456"
        service_session = self.Session()
        self.addCleanup(service_session.close)

        def override_service(client_factory):
            app.dependency_overrides[get_deepseek_settings_service] = lambda: DeepSeekSettingsService(
                db=service_session,
                workspace_id=self.workspace_id,
                settings=self.settings,
                cipher=CredentialCipher(self.settings.credential_encryption_key),
                transport=self.transport,
                client_factory=client_factory,
            )

        override_service(lambda _: SuccessfulClient())
        public_key = self.client.get("/api/v1/settings/deepseek/encryption-key").json()["public_key"]
        encrypted = encrypt_for_transport(public_key, api_key)
        success = self.client.post("/api/v1/settings/deepseek/test", json={"encrypted_api_key": encrypted})
        self.assertEqual(success.json(), {"success": True, "message": "连接成功，DeepSeek API 可用"})
        self.assertNotIn(api_key, success.text)

        override_service(lambda _: RejectedClient())
        failure = self.client.post("/api/v1/settings/deepseek/test", json={"encrypted_api_key": encrypted})
        self.assertEqual(failure.json(), {"success": False, "message": "连接失败：API Key 无效"})
        self.assertNotIn(api_key, failure.text)

    def test_vercel_origin_and_authorization_header_pass_cors_preflight(self) -> None:
        production_origin = "https://revia-test.vercel.app"
        original = app.user_middleware
        self.assertTrue(original)
        for origin in ("http://localhost:3000", "http://127.0.0.1:3000", "http://[::1]:3000"):
            response = self.client.options(
                "/api/v1/settings/deepseek",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["access-control-allow-origin"], origin)
        parsed = Settings(_env_file=None, cors_origins=production_origin).cors_origins
        self.assertEqual(parsed, [production_origin])


if __name__ == "__main__":
    unittest.main()
