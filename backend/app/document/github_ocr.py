from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import httpx


_LOGGER = logging.getLogger("revia.ocr.github")


class GitHubOCRDispatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubOCRDispatchResult:
    workflow_run_id: int | None
    run_url: str | None


class GitHubOCRDispatcher:
    """Starts one repository workflow for one OCR attempt."""

    def __init__(
        self,
        *,
        token: str,
        repository: str,
        workflow: str = "revia-ocr.yml",
        ref: str = "main",
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        normalized_repository = repository.strip().strip("/")
        if normalized_repository.count("/") != 1:
            raise ValueError("repository must use OWNER/REPO format")
        if not token.strip():
            raise ValueError("GitHub OCR token cannot be empty")
        if not workflow.strip() or not ref.strip() or timeout_seconds <= 0:
            raise ValueError("GitHub OCR workflow settings are invalid")
        self._endpoint = (
            f"https://api.github.com/repos/{normalized_repository}/actions/workflows/"
            f"{workflow.strip()}/dispatches"
        )
        self._token = token.strip()
        self._ref = ref.strip()
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client

    def dispatch(self, document_id: uuid.UUID, attempt_id: uuid.UUID) -> GitHubOCRDispatchResult:
        try:
            response = self._get_client().post(
                self._endpoint,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "ref": self._ref,
                    "inputs": {
                        "document_id": str(document_id),
                        "attempt_id": str(attempt_id),
                    },
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            _LOGGER.warning("github_ocr_dispatch_unavailable error_type=%s", exc.__class__.__name__)
            raise GitHubOCRDispatchError("GitHub OCR 任务暂时无法启动") from exc
        except httpx.HTTPError as exc:
            _LOGGER.warning("github_ocr_dispatch_http_error error_type=%s", exc.__class__.__name__)
            raise GitHubOCRDispatchError("GitHub OCR 任务请求失败") from exc

        if response.status_code not in {200, 204}:
            _LOGGER.warning("github_ocr_dispatch_rejected status=%d", response.status_code)
            if response.status_code in {401, 403}:
                raise GitHubOCRDispatchError("GitHub OCR 触发凭据无效或权限不足")
            if response.status_code == 404:
                raise GitHubOCRDispatchError("GitHub OCR 工作流不存在或尚未启用")
            if response.status_code == 422:
                raise GitHubOCRDispatchError("GitHub OCR 工作流参数或分支配置无效")
            raise GitHubOCRDispatchError(f"GitHub OCR 任务启动失败（status={response.status_code}）")

        run_id: int | None = None
        run_url: str | None = None
        if response.content:
            try:
                payload = response.json()
                raw_run_id = payload.get("workflow_run_id")
                run_id = int(raw_run_id) if raw_run_id is not None else None
                run_url = str(payload.get("html_url") or payload.get("run_url") or "").strip() or None
            except (ValueError, TypeError, AttributeError):
                pass
        _LOGGER.info(
            "github_ocr_dispatched document_id=%s attempt_id=%s workflow_run_id=%s",
            document_id,
            attempt_id,
            run_id or "unknown",
        )
        return GitHubOCRDispatchResult(workflow_run_id=run_id, run_url=run_url)

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self._timeout_seconds, connect=min(10.0, self._timeout_seconds)),
                follow_redirects=True,
            )
        return self._client
