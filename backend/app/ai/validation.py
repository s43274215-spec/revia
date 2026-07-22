import json
import logging
import re
import uuid
from copy import deepcopy

from pydantic import ValidationError

from app.ai.schemas import (
    GeneratedBulletPayload,
    GeneratedItemResult,
    GeneratedProject,
    KEYWORDS_MAX_ITEMS,
    KEYWORDS_MAX_LENGTH,
    KEYWORDS_RECOMMENDED_MAX_ITEMS,
    KEYWORDS_RECOMMENDED_MIN_ITEMS,
    ORIGINAL_MAX_LENGTH,
    ORIGINAL_RECOMMENDED_LENGTH,
    RECITATION_MAX_LENGTH,
    RECITATION_RECOMMENDED_LENGTH,
    TITLE_MAX_LENGTH,
    TITLE_RECOMMENDED_MAX_LENGTH,
    normalize_title_for_comparison,
)
from app.matching.schemas import CandidateChunk

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
    # Parent/child overlap is valid learning content. Keep the AI title unchanged;
    # the reading page and Word export hide only titles that are effectively equal.
    return title.strip()


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


_SALVAGE_NOISE_MARKERS = (
    "严禁复制",
    "复制此链接",
    "mininunversity",
    "hinannvvesiy",
    "qkc:/",
    "http://",
    "https://",
)


def _deduplicate_warnings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        cleaned = " ".join(value.split()).strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _readable_text(value: object, *, keywords: bool = False) -> str:
    normalized = _content_text(value, keywords=keywords)
    if not isinstance(normalized, str):
        return ""
    cleaned = normalized.strip()
    if len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", cleaned)) < 2:
        return ""
    lowered = cleaned.casefold()
    if any(marker in lowered for marker in _SALVAGE_NOISE_MARKERS):
        return ""
    return cleaned


def _safe_title(value: object, fallback: str) -> str:
    title = _readable_text(value) or _readable_text(fallback)
    return title[:TITLE_MAX_LENGTH].strip() if title else ""


def _version_content(bullet: dict[str, object], key: str) -> str:
    value = bullet.get(key)
    if isinstance(value, dict):
        value = value.get("content")
    return _readable_text(value, keywords=key == "keywords")


_JSON_LIKE_KEY = re.compile(
    r"(?i)(?:[\"'](?P<quoted>[^\"']+)[\"']|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))\s*:\s*"
)


def _decode_json_like_string(value: str) -> str:
    try:
        decoded = json.loads(f'"{value}"')
        return decoded if isinstance(decoded, str) else value
    except json.JSONDecodeError:
        return (
            value
            .replace(r"\n", "\n")
            .replace(r"\r", "\n")
            .replace(r"\t", " ")
            .replace(r'\"', '"')
            .replace(r"\'", "'")
            .replace(r"\\", "\\")
        )


def _json_like_value_after(text: str, start: int) -> str:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        return ""

    quote = text[index]
    if quote in {'"', "'"}:
        index += 1
        buffer: list[str] = []
        escaped = False
        while index < len(text):
            char = text[index]
            if escaped:
                buffer.extend(("\\", char))
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                break
            else:
                buffer.append(char)
            index += 1
        return _decode_json_like_string("".join(buffer))

    if text[index] == "[":
        end = text.find("]", index + 1)
        segment = text[index + 1 : end if end >= 0 else len(text)]
        items = [
            _decode_json_like_string(match.group("value"))
            for match in re.finditer(
                r"(?P<quote>[\"'])(?P<value>.*?)(?<!\\)(?P=quote)",
                segment,
                flags=re.DOTALL,
            )
        ]
        return "、".join(item.strip() for item in items if item.strip())

    end = index
    while end < len(text) and text[end] not in ",}\r\n":
        end += 1
    return text[index:end].strip()


def _json_like_values(text: str, key: str) -> list[str]:
    values: list[str] = []
    for match in _JSON_LIKE_KEY.finditer(text):
        found_key = match.group("quoted") or match.group("plain") or ""
        if found_key.casefold() != key.casefold():
            continue
        value = _json_like_value_after(text, match.end())
        if value:
            values.append(value)
    return values


def _recover_broken_json_payload(raw_output: str, *, fallback_title: str) -> dict[str, object] | None:
    """Recover only explicit version content strings from malformed JSON-like output.

    The fallback never treats JSON keys or arbitrary surrounding prose as learning
    content. Missing versions are completed later by the normal deterministic
    salvage path, and source references remain empty because their association
    cannot be verified after structural corruption.
    """
    version_matches = list(re.finditer(
        r"(?i)(?:[\"'](?P<quoted>original|recitation|keywords)[\"']|"
        r"(?P<plain>original|recitation|keywords))\s*:\s*",
        raw_output,
    ))
    recovered: dict[str, list[str]] = {
        "original": [],
        "recitation": [],
        "keywords": [],
    }

    for index, match in enumerate(version_matches):
        version = (match.group("quoted") or match.group("plain") or "").casefold()
        segment_end = (
            version_matches[index + 1].start()
            if index + 1 < len(version_matches)
            else len(raw_output)
        )
        segment = raw_output[match.end() : segment_end]
        values = _json_like_values(segment, "content")
        if not values:
            continue
        text = _readable_text(values[0], keywords=version == "keywords")
        if text:
            recovered[version].append(text)

    count = max((len(values) for values in recovered.values()), default=0)
    bullets: list[dict[str, object]] = []
    for index in range(count):
        original = recovered["original"][index] if index < len(recovered["original"]) else ""
        recitation = recovered["recitation"][index] if index < len(recovered["recitation"]) else ""
        keywords = recovered["keywords"][index] if index < len(recovered["keywords"]) else ""
        if not (original or recitation or keywords):
            continue
        bullets.append({
            "title": fallback_title if count == 1 else f"要点 {index + 1}",
            "original": original,
            "recitation": recitation,
            "keywords": keywords,
            "source_chunk_ids": [],
            "source_pages": [],
        })

    if not bullets:
        return None
    return {
        "knowledge_point_title": fallback_title,
        "bullet_points": bullets,
    }


def _collect_format_warnings(result: GeneratedItemResult) -> list[str]:
    warnings = list(result.format_warnings)
    parent = normalize_title_for_comparison(result.knowledge_point_title)
    for bullet in result.bullet_points:
        if bullet.original.content.strip() == bullet.recitation.content.strip():
            warnings.append("背诵版与原文版内容相同")
        if parent and parent == normalize_title_for_comparison(bullet.title):
            warnings.append("要点标题与知识点标题相同，展示时已隐藏")
        if not bullet.source_chunk_ids or not bullet.source_pages:
            warnings.append("来源未完整验证")
    return _deduplicate_warnings(warnings)


def salvage_generated_item(
    raw_outputs: list[str],
    *,
    fallback_title: str,
    candidates: list[CandidateChunk],
) -> GeneratedItemResult:
    """Preserve readable AI output after the one normal repair attempt failed.

    This function only reshapes or reuses returned text. It never invents source
    citations or new learning claims.
    """
    allowed_chunk_ids = {candidate.chunk_id for candidate in candidates}
    allowed_pages = {
        page
        for candidate in candidates
        for page in range(candidate.page_start, candidate.page_end + 1)
    }
    last_error: Exception | None = None

    for raw_output in raw_outputs:
        recovered_from_broken_json = False
        try:
            normalized = normalize_generated_item_payload(
                extract_json(raw_output),
                fallback_title=fallback_title,
            )
        except AIOutputValidationError as exc:
            last_error = exc
            normalized = _recover_broken_json_payload(
                raw_output,
                fallback_title=fallback_title,
            )
            if normalized is None:
                continue
            recovered_from_broken_json = True
        if not isinstance(normalized, dict):
            continue

        knowledge_title = _safe_title(normalized.get("knowledge_point_title"), fallback_title)
        if not knowledge_title:
            last_error = AIOutputValidationError("AI output did not contain a readable knowledge point title")
            continue

        raw_bullets = normalized.get("bullet_points")
        if isinstance(raw_bullets, dict):
            raw_bullets = [raw_bullets]
        if not isinstance(raw_bullets, list):
            raw_bullets = []

        warnings = ["部分格式异常，已保留可读内容"]
        if recovered_from_broken_json:
            warnings.append("AI 返回结构损坏，已从可读文本中恢复内容")
        salvaged_bullets: list[dict[str, object]] = []
        for index, value in enumerate(raw_bullets):
            if not isinstance(value, dict):
                warnings.append("已忽略无法读取的要点")
                continue
            bullet = dict(value)
            title_candidates: list[object] = [bullet.get("title")]
            for key in ("original", "recitation", "keywords"):
                version = bullet.get(key)
                if isinstance(version, dict):
                    title_candidates.append(version.get("title"))
            title = next((_safe_title(candidate, "") for candidate in title_candidates if _safe_title(candidate, "")), "")
            if not title:
                title = f"要点 {index + 1}"
                warnings.append("部分要点缺少标题，已使用中性标题")

            original = _version_content(bullet, "original")
            recitation = _version_content(bullet, "recitation")
            keywords = _version_content(bullet, "keywords")
            available = original or recitation or keywords
            if not available:
                warnings.append("已忽略没有可读内容的要点")
                continue
            if not original:
                original = available
                warnings.append("原文版缺失，已使用现有可读内容回退")
            if not recitation:
                recitation = original
                warnings.append("背诵版缺失，已使用现有可读内容回退")
            if not keywords:
                keywords = title or available
                warnings.append("关键词版缺失，已使用现有可读内容回退")

            if len(original) > ORIGINAL_MAX_LENGTH:
                original = original[:ORIGINAL_MAX_LENGTH]
                warnings.append("原文版超过安全长度，已保留前部可读内容")
            if len(recitation) > RECITATION_MAX_LENGTH:
                recitation = recitation[:RECITATION_MAX_LENGTH]
                warnings.append("背诵版超过安全长度，已保留前部可读内容")
            if len(keywords) > KEYWORDS_MAX_LENGTH:
                keywords = keywords[:KEYWORDS_MAX_LENGTH]
                warnings.append("关键词版超过安全长度，已保留前部可读内容")

            keyword_items = [
                item.strip()
                for item in re.split(r"[\n、，,；;|/]+", keywords)
                if item.strip()
            ]
            if len(keyword_items) > KEYWORDS_MAX_ITEMS:
                keywords = "、".join(keyword_items[:KEYWORDS_MAX_ITEMS])
                warnings.append("关键词数量超过安全上限，已保留前 50 项")

            raw_ids = _as_list(bullet.get("source_chunk_ids"))
            source_ids: list[uuid.UUID] = []
            for raw_id in raw_ids:
                try:
                    parsed = uuid.UUID(str(raw_id))
                except (ValueError, TypeError, AttributeError):
                    continue
                if parsed in allowed_chunk_ids and parsed not in source_ids:
                    source_ids.append(parsed)
            raw_pages = _as_list(bullet.get("source_pages"))
            source_pages: list[int] = []
            for raw_page in raw_pages:
                try:
                    page = int(str(raw_page).strip())
                except (ValueError, TypeError, AttributeError):
                    continue
                if page in allowed_pages and page not in source_pages:
                    source_pages.append(page)
            if len(source_ids) != len(raw_ids) or len(source_pages) != len(raw_pages):
                warnings.append("部分无效来源已移除")
            if not source_ids or not source_pages:
                warnings.append("来源未完整验证")

            candidate_payload = {
                "title": title,
                "original": {"title": title, "content": original},
                "recitation": {"title": title, "content": recitation},
                "keywords": {"title": title, "content": keywords},
                "source_chunk_ids": source_ids,
                "source_pages": source_pages,
            }
            try:
                validated = GeneratedBulletPayload.model_validate(candidate_payload)
            except ValidationError:
                candidate_payload["title"] = f"要点 {index + 1}"
                for key in ("original", "recitation", "keywords"):
                    candidate_payload[key]["title"] = candidate_payload["title"]  # type: ignore[index]
                try:
                    validated = GeneratedBulletPayload.model_validate(candidate_payload)
                    warnings.append("部分要点标题异常，已使用中性标题")
                except ValidationError as exc:
                    last_error = exc
                    warnings.append("已忽略仍无法安全保存的要点")
                    continue
            salvaged_bullets.append(validated.model_dump(mode="json"))

        if not salvaged_bullets:
            last_error = AIOutputValidationError("AI output did not contain any readable bullet content")
            continue

        try:
            result = GeneratedItemResult.model_validate({
                "knowledge_point_title": knowledge_title,
                "bullet_points": salvaged_bullets,
                "format_warnings": _deduplicate_warnings(warnings),
            })
        except ValidationError as exc:
            last_error = exc
            continue
        return result.model_copy(update={"format_warnings": _collect_format_warnings(result)})

    raise AIOutputValidationError(
        "AI output contained no readable learning content after deterministic salvage"
    ) from last_error


def _log_soft_format_warnings(result: GeneratedItemResult) -> None:
    if len(result.knowledge_point_title) > TITLE_RECOMMENDED_MAX_LENGTH:
        logger.warning(
            "AI item accepted with soft format warning knowledge title length=%d exceeds recommended %d: %s",
            len(result.knowledge_point_title),
            TITLE_RECOMMENDED_MAX_LENGTH,
            result.knowledge_point_title,
        )
    for bullet_index, bullet in enumerate(result.bullet_points):
        keyword_count = len([
            item
            for item in re.split(r"[\n、，,；;]+", bullet.keywords.content)
            if item.strip()
        ])
        warnings: list[str] = []
        if len(bullet.title) > TITLE_RECOMMENDED_MAX_LENGTH:
            warnings.append(
                f"title length {len(bullet.title)} exceeds recommended {TITLE_RECOMMENDED_MAX_LENGTH}"
            )
        if len(bullet.original.content) > ORIGINAL_RECOMMENDED_LENGTH:
            warnings.append(
                f"original length {len(bullet.original.content)} exceeds recommended {ORIGINAL_RECOMMENDED_LENGTH}"
            )
        if len(bullet.recitation.content) > RECITATION_RECOMMENDED_LENGTH:
            warnings.append(
                f"recitation length {len(bullet.recitation.content)} exceeds recommended {RECITATION_RECOMMENDED_LENGTH}"
            )
        if not KEYWORDS_RECOMMENDED_MIN_ITEMS <= keyword_count <= KEYWORDS_RECOMMENDED_MAX_ITEMS:
            warnings.append(
                f"keyword count {keyword_count} is outside recommended "
                f"{KEYWORDS_RECOMMENDED_MIN_ITEMS}-{KEYWORDS_RECOMMENDED_MAX_ITEMS}"
            )
        if warnings:
            logger.warning(
                "AI item accepted with soft format warning bullet=%d title=%s: %s",
                bullet_index,
                bullet.title,
                "; ".join(warnings),
            )


def validate_generated_item(raw_output: str, *, fallback_title: str | None = None) -> GeneratedItemResult:
    try:
        payload = normalize_generated_item_payload(extract_json(raw_output), fallback_title=fallback_title)
        result = GeneratedItemResult.model_validate(payload)
        result = result.model_copy(update={"format_warnings": _collect_format_warnings(result)})
        _log_soft_format_warnings(result)
        return result
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
