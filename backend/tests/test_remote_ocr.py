from __future__ import annotations

import unittest
from unittest.mock import Mock

import httpx
from pydantic import ValidationError

from app.api.v1.endpoints.documents import build_document_processing_service
from app.core.config import Settings
from app.document.ocr import OCRWorkerError, OCRWorkerResourceError
from app.document.remote_ocr import RemoteOCREngine
from app.document.parser import PDFParser


class RemoteOCREngineTests(unittest.TestCase):
    @staticmethod
    def _client(handler):
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_sends_png_with_bearer_key_and_returns_text(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/ocr")
            self.assertEqual(request.headers["authorization"], "Bearer shared-secret")
            self.assertEqual(request.headers["content-type"], "image/png")
            self.assertEqual(request.content, b"png-data")
            return httpx.Response(200, json={"text": "第一页文字", "engine_version": "3.4.1"})

        client = self._client(handler)
        engine = RemoteOCREngine(
            base_url="https://example.hf.space/",
            api_key="shared-secret",
            client=client,
        )
        try:
            self.assertEqual(engine.recognize(b"png-data"), "第一页文字")
            self.assertEqual(engine.version, "remote RapidOCR 3.4.1")
        finally:
            client.close()

    def test_service_failure_waits_for_manual_resume(self) -> None:
        client = self._client(lambda request: httpx.Response(503, json={"detail": "warming"}))
        engine = RemoteOCREngine(
            base_url="https://example.hf.space",
            api_key="shared-secret",
            client=client,
        )
        try:
            with self.assertRaises(OCRWorkerResourceError):
                engine.recognize(b"png-data")
        finally:
            client.close()

    def test_bad_response_is_not_silently_accepted(self) -> None:
        client = self._client(lambda request: httpx.Response(200, text="not-json"))
        engine = RemoteOCREngine(
            base_url="https://example.hf.space",
            api_key="shared-secret",
            client=client,
        )
        try:
            with self.assertRaises(OCRWorkerError):
                engine.recognize(b"png-data")
        finally:
            client.close()


    def test_owned_client_can_be_recreated_after_parser_close(self) -> None:
        engine = RemoteOCREngine(
            base_url="https://example.hf.space",
            api_key="shared-secret",
        )
        engine.close()
        self.assertIsNone(engine._client)

    def test_parser_closes_remote_engine(self) -> None:
        engine = Mock()
        parser = PDFParser(ocr_engine=engine)
        parser.close()
        engine.close.assert_called_once_with()


class RemoteOCRWiringTests(unittest.TestCase):
    def test_processing_service_uses_remote_engine_when_configured(self) -> None:
        settings = Settings(
            _env_file=None,
            remote_ocr_url="https://example.hf.space",
            remote_ocr_api_key="secret",
        )
        service = build_document_processing_service(Mock(), settings)
        self.assertIsInstance(service._parser._ocr_engine, RemoteOCREngine)

    def test_processing_service_keeps_local_worker_fallback_without_remote_url(self) -> None:
        service = build_document_processing_service(Mock(), Settings(_env_file=None))
        self.assertIsNone(service._parser._ocr_engine)


class RemoteOCRSettingsTests(unittest.TestCase):
    def test_url_and_key_must_be_configured_together(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, remote_ocr_url="https://example.hf.space")
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, remote_ocr_api_key="secret")

    def test_complete_remote_configuration_is_accepted(self) -> None:
        settings = Settings(
            _env_file=None,
            remote_ocr_url="https://example.hf.space",
            remote_ocr_api_key="secret",
            remote_ocr_timeout_seconds=360,
        )
        self.assertEqual(settings.remote_ocr_timeout_seconds, 360)


if __name__ == "__main__":
    unittest.main()
