from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class OCRServiceAPITests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["OCR_API_KEY"] = "test-secret"
        import ocr_service.app as module
        self.module = importlib.reload(module)
        self.client = TestClient(self.module.app)

    def tearDown(self) -> None:
        self.client.close()
        os.environ.pop("OCR_API_KEY", None)

    def test_health_does_not_expose_secret(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["api_key_configured"])
        self.assertNotIn("test-secret", response.text)

    def test_authentication_is_required(self) -> None:
        response = self.client.post(
            "/v1/ocr",
            content=b"image",
            headers={"Content-Type": "image/png"},
        )
        self.assertEqual(response.status_code, 401)

    def test_recognizes_one_image_at_a_time(self) -> None:
        with (
            patch.object(self.module, "_get_engine", return_value=object()),
            patch.object(self.module, "_recognize_sync", return_value="识别文字"),
            patch.object(self.module, "_rapidocr_version", return_value="3.4.1"),
        ):
            response = self.client.post(
                "/v1/ocr",
                content=b"image",
                headers={
                    "Content-Type": "image/png",
                    "Authorization": "Bearer test-secret",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"], "识别文字")


if __name__ == "__main__":
    unittest.main()
