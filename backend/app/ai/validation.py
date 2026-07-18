import json
import logging
import re
from copy import deepcopy

from pydantic import ValidationError

from app.ai.schemas import GeneratedItemResult, GeneratedProject

logger = logging.getLogger("revia.ai.validation")


class AIOutputValidationError(ValueError):
    pass


def validate_generated_project(raw_output: str) -> GeneratedProject:
    try:
        payload = json.loads(raw_output)
        return GeneratedProject.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AIOutputValidationError("AI output does not match the Revia learning material schema") from exc


def extract_json(raw_output: str) -> object:
    value = raw_output.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end < start:
        logger.error("AI JSON extraction failed: no JSON object was found")
        raise AIOutputValidationError("AI output did not contain a JSON object")
    try:
        return json.loads(value[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.error("AI JSON extraction failed at line=%d column=%d: %s", exc.lineno, exc.colno, exc.msg)
        raise AIOutputValidationError("AI output contained invalid JSON") from exc


def _content_text(value: object, *, keywords: bool = False) -> object:
    if isinstance(value, list):
        if not all(isinstance(item, (str, int, float)) or item is None for item in value):
            return value
        parts = [str(item).strip() for item in value if item is not None and str(item).strip()]
        return ("、" if keywords else "\n\n").join(parts)
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    if keywords:
        parts = [part.strip() for part in re.split(r"[、，,；;|/\n]+", cleaned) if part.strip()]
        return "、".join(parts)
    return re.sub(r"[ \t]+", " ", cleaned)


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _deduplicate(values: list[object]) -> list[object]:
    unique: list[object] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _normalized_child_title(parent: str, title: str, index: int) -> str:
    cleaned = title.strip()
    normalized_parent = re.sub(r"[^\w\u4e00-\u9fff]+", "", parent, flags=re.UNICODE).casefold()
    normalized_child = re.sub(r"[^\w\u4e00-\u9fff]+", "", cleaned, flags=re.UNICODE).casefold()
    if normalized_parent and normalized_parent in normalized_child:
        remainder = cleaned.replace(parent, "").strip(" ：:·-—（）()")
        remainder = re.sub(r"^[（(]?[一二三四五六七八九十\d]+[）).、]?\s*", "", remainder).strip()
        if len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", remainder)) >= 2:
            return remainder
        return "核心说明" if index == 0 else f"要点{index + 1}"
    return cleaned


def normalize_generated_item_payload(payload: object, *, fallback_title: str | None = None) -> object:
    """Repair deterministic shape drift without inventing learning content."""
    if not isinstance(payload, dict):
        return payload
    normalized = deepcopy(payload)
    if "bullet_points" not in normalized and any(
        key in normalized for key in ("original", "recitation", "keywords", "source_chunk_ids", "source_pages")
    ):
        bullet = dict(normalized)
        parent = str(bullet.pop("knowledge_point_title", "") or fallback_title or "").strip()
        normalized = {"knowledge_point_title": parent, "bullet_points": [bullet]}

    parent = normalized.get("knowledge_point_title")
    if not isinstance(parent, str) or not parent.strip():
        normalized["knowledge_point_title"] = (fallback_title or "").strip()
    else:
        normalized["knowledge_point_title"] = parent.strip()
    parent_title = str(normalized.get("knowledge_point_title") or "")

    bullets = normalized.get("bullet_points")
    if isinstance(bullets, dict):
        bullets = [bullets]
    if not isinstance(bullets, list):
        return normalized
    normalized_bullets: list[object] = []
    for index, value in enumerate(bullets):
        if not isinstance(value, dict):
            normalized_bullets.append(value)
            continue
        bullet = dict(value)
        version_title = next((
            version.get("title")
            for key in ("original", "recitation", "keywords")
            if isinstance((version := bullet.get(key)), dict)
            and isinstance(version.get("title"), str)
            and version.get("title", "").strip()
        ), None)
        raw_title = bullet.get("title")
        title = str(raw_title or version_title or fallback_title or "").strip()
        title = _normalized_child_title(parent_title, title, index)
        bullet["title"] = title
        for key in ("original", "recitation", "keywords"):
            version = bullet.get(key)
            if isinstance(version, dict):
                version = dict(version)
                version["title"] = title
                version["content"] = _content_text(version.get("content"), keywords=key == "keywords")
            else:
                version = {"title": title, "content": _content_text(version, keywords=key == "keywords")}
            bullet[key] = version
        bullet["source_chunk_ids"] = _deduplicate(_as_list(bullet.get("source_chunk_ids")))
        pages: list[object] = []
        for page in _as_list(bullet.get("source_pages")):
            if isinstance(page, str) and page.strip().isdigit():
                page = int(page.strip())
            if isinstance(page, int) and page > 0:
                pages.append(page)
            elif page is not None:
                pages.append(page)
        bullet["source_pages"] = _deduplicate(pages)
        normalized_bullets.append(bullet)
    normalized["bullet_points"] = normalized_bullets
    return normalized


def validate_generated_item(raw_output: str, *, fallback_title: str | None = None) -> GeneratedItemResult:
    try:
        payload = normalize_generated_item_payload(extract_json(raw_output), fallback_title=fallback_title)
        return GeneratedItemResult.model_validate(payload)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False)[:8]
        )
        logger.error(
            "AI item Pydantic validation failed: %s",
            json.dumps(exc.errors(include_url=False), ensure_ascii=False, default=str),
        )
        raise AIOutputValidationError(
            f"AI output does not match the three-version item schema: {details}"
        ) from exc
