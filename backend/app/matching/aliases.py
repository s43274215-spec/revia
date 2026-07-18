import re
import unicodedata

from app.matching.query_config import CONFIGURED_ALIAS_GROUPS, CONFIGURED_TOPIC_EXPANSIONS


def canonicalize_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"[‐‑‒–—―−﹣－]+", "-", normalized)
    normalized = normalized.replace("／", "/")
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_query_key(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", canonicalize_query(value), flags=re.UNICODE).casefold()


def alias_queries(value: str) -> tuple[str, ...]:
    key = normalize_query_key(value)
    expanded: list[str] = []
    for group in CONFIGURED_ALIAS_GROUPS:
        if any(normalize_query_key(alias) in key or key in normalize_query_key(alias) for alias in group if key):
            expanded.extend(group)
    return _unique(expanded)


def configured_subqueries(value: str) -> tuple[str, ...]:
    key = normalize_query_key(value)
    expanded: list[str] = []
    for required_terms, queries in CONFIGURED_TOPIC_EXPANSIONS:
        if all(normalize_query_key(term) in key for term in required_terms):
            expanded.extend(queries)
    return _unique(expanded)


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = canonicalize_query(value)
        key = normalize_query_key(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return tuple(result)
