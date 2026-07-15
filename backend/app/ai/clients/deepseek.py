import json
import logging
import time
from datetime import UTC, datetime

import httpx

from app.ai.clients.base import AIClient, AIRequestError

logger = logging.getLogger("revia.ai.deepseek")


class DeepSeekClient(AIClient):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        temperature: float,
    ) -> None:
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._timeout = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature

    async def generate_completion(self, *, system_prompt: str, user_prompt: str) -> str:
        requested_at = datetime.now(UTC).isoformat()
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        logger.info(
            "DeepSeek request started requested_at=%s model=%s url=%s system_chars=%d user_chars=%d "
            "has_syllabus_item=%s has_candidate_text=%s requires_three_versions=%s",
            requested_at,
            self._model,
            self._url,
            len(system_prompt),
            len(user_prompt),
            "当前考纲条目：" in user_prompt,
            "SOURCE_CONTEXT_JSON_START" in user_prompt and "SOURCE_CONTEXT_JSON_START\n[]" not in user_prompt,
            all(version in user_prompt for version in ("original", "recitation", "keywords")),
        )
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            logger.info(
                "DeepSeek response received requested_at=%s model=%s status=%d elapsed_ms=%d request_id=%s",
                requested_at,
                self._model,
                response.status_code,
                elapsed_ms,
                response.headers.get("x-request-id") or response.headers.get("x-ds-request-id"),
            )
            if response.status_code == 429:
                raise AIRequestError("AI provider rate limit was reached")
            if response.is_error:
                logger.error(
                    "DeepSeek HTTP error status=%d body=%s",
                    response.status_code,
                    response.text[:500],
                )
            response.raise_for_status()
            data = response.json()
            logger.info("DeepSeek token usage=%s", json.dumps(data.get("usage") or {}, ensure_ascii=False))
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise AIRequestError("AI provider returned an empty response")
            logger.info("DeepSeek content received chars=%d", len(content))
            return content
        except httpx.TimeoutException as exc:
            raise AIRequestError("AI provider request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise AIRequestError(f"AI provider request failed with status {exc.response.status_code}") from exc
        except (httpx.RequestError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIRequestError("AI provider returned an invalid or unreachable response") from exc
