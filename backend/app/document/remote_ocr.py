from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version

import httpx

from app.document.ocr import OCRWorkerError, OCRWorkerResourceError


_LOGGER = logging.getLogger("revia.ocr.remote")


class RemoteOCREngine:
    """HTTP client for the isolated Revia OCR service.

    The main backend renders only the current PDF page and sends that PNG to the
    OCR service. The original PDF and Backblaze credentials never leave the main
    backend, so a failed OCR request does not trigger another object-store download.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 300.0,
        max_image_bytes: int = 20 * 1024 * 1024,
        client: httpx.Client | None = None,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/")
        if not normalized_url:
            raise ValueError("REMOTE_OCR_URL 不能为空")
        if not api_key.strip():
            raise ValueError("REMOTE_OCR_API_KEY 不能为空")
        if timeout_seconds <= 0 or max_image_bytes <= 0:
            raise ValueError("远程 OCR 限制必须为正数")
        self._endpoint = f"{normalized_url}/v1/ocr"
        self._api_key = api_key.strip()
        self._max_image_bytes = max_image_bytes
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client: httpx.Client | None = client
        self._version = "remote"

    @property
    def version(self) -> str:
        return self._version

    def recognize(self, image: bytes) -> str:
        if not image:
            raise OCRWorkerError("OCR 页面图像为空")
        if len(image) > self._max_image_bytes:
            raise OCRWorkerError(
                f"OCR 页面图像超过 {self._max_image_bytes // (1024 * 1024)}MB 限制"
            )
        try:
            response = self._get_client().post(
                self._endpoint,
                content=image,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "image/png",
                    "Accept": "application/json",
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            _LOGGER.warning("remote_ocr_unavailable error_type=%s", exc.__class__.__name__)
            raise OCRWorkerResourceError("远程 OCR 服务暂时不可用") from exc
        except httpx.HTTPError as exc:
            _LOGGER.warning("remote_ocr_http_error error_type=%s", exc.__class__.__name__)
            raise OCRWorkerResourceError("远程 OCR 服务请求失败") from exc

        if response.status_code in {401, 403}:
            _LOGGER.error("remote_ocr_auth_failed status=%d", response.status_code)
            raise OCRWorkerResourceError("远程 OCR 服务鉴权失败")
        if response.status_code == 429 or response.status_code >= 500:
            _LOGGER.warning("remote_ocr_unavailable status=%d", response.status_code)
            raise OCRWorkerResourceError("远程 OCR 服务暂时不可用")
        if response.status_code >= 400:
            _LOGGER.warning("remote_ocr_rejected status=%d", response.status_code)
            raise OCRWorkerError(f"远程 OCR 服务拒绝请求（status={response.status_code}）")

        try:
            payload = response.json()
            text = str(payload.get("text") or "")
            service_version = str(payload.get("engine_version") or "").strip()
        except (ValueError, AttributeError, TypeError) as exc:
            raise OCRWorkerError("远程 OCR 服务返回格式无效") from exc
        if service_version:
            self._version = f"remote RapidOCR {service_version}"
        return text

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(
                    self._timeout_seconds,
                    connect=min(30.0, self._timeout_seconds),
                ),
                follow_redirects=True,
            )
        return self._client
