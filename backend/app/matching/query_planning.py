import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.matching.aliases import alias_queries, canonicalize_query, configured_subqueries, normalize_query_key
from app.matching.preprocessing import MatchingQueryPreprocessor
from app.syllabus.parser import ParsedSyllabusItem


class SyllabusItemType(StrEnum):
    KNOWLEDGE = "KNOWLEDGE"
    COMPOSITE = "COMPOSITE"
    COLLECTION = "COLLECTION"
    TASK = "TASK"


class HierarchyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chapter: str | None = None
    parent_title: str | None = None
    child_titles: tuple[str, ...] = ()
    related_titles: tuple[str, ...] = ()


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_type: SyllabusItemType
    original_query: str
    normalized_queries: tuple[str, ...]
    subqueries: tuple[str, ...]
    hierarchy_context: HierarchyContext
    used_ai_fallback: bool = False

    @property
    def retrieval_queries(self) -> tuple[str, ...]:
        return _unique((*self.normalized_queries, *self.subqueries))

    def with_ai_queries(self, queries: list[str]) -> "QueryPlan":
        return self.model_copy(update={
            "normalized_queries": _unique((*self.normalized_queries, *queries)),
            "used_ai_fallback": True,
        })


_TASK_MARKERS = re.compile(r"案例|论述|应用|作答|分析题|结合.+模型|答题|^相关计算|^计算题")
_COLLECTION_MARKERS = re.compile(r"^(?:各|经典|主要|核心).*(?:考点|理论|方法|模型)$|等(?:基础|主要|常见)?(?:方法|内容|理论)$")
_INSTRUCTION_FRAGMENTS = re.compile(
    r"^(?:需|应|能够|要求)?(?:重点)?掌握|理解即可|了解即可|无需.*|不能.*|注意.*|是核心考点|整体难度.*|对应课堂.*"
    r"|^二者的?(?:核心)?区别|^各自的?(?:范畴|内容|内涵)"
)
_SPLIT_CONNECTORS = re.compile(r"[、,，;；/]|(?<=[\u4e00-\u9fffA-Za-z0-9])(?:以及|及|与|和)(?=[\u4e00-\u9fffA-Za-z0-9])")
_COMPARISON_SUFFIX = re.compile(r"(?:的)?(?:联系与区别|区别与联系|区别|差异|异同|比较)$")
_SHARED_SUFFIX_BOUNDARIES = frozenset("性型式类期侧方部")


class QueryPlanner:
    def __init__(self, preprocessor: MatchingQueryPreprocessor | None = None) -> None:
        self._preprocessor = preprocessor or MatchingQueryPreprocessor()

    def plan_items(self, entries: list[ParsedSyllabusItem]) -> list[QueryPlan]:
        children_by_parent: dict[str, list[str]] = {}
        for entry in entries:
            if entry.parent_title:
                children_by_parent.setdefault(normalize_query_key(entry.parent_title), []).append(entry.title)

        plans: list[QueryPlan] = []
        for index, entry in enumerate(entries):
            children = tuple(children_by_parent.get(normalize_query_key(entry.title), ()))
            related = self._related_titles(entries, index)
            plans.append(self.plan_item(
                entry.title,
                chapter=entry.chapter,
                parent_title=entry.parent_title,
                child_titles=children,
                related_titles=related,
            ))
        return plans

    def plan_item(
        self,
        syllabus_item: str,
        *,
        chapter: str | None = None,
        parent_title: str | None = None,
        child_titles: tuple[str, ...] = (),
        related_titles: tuple[str, ...] = (),
    ) -> QueryPlan:
        original = syllabus_item.strip()
        prepared = self._preprocessor.prepare(original)
        configured = configured_subqueries(original)
        generic = self._split_subqueries(prepared.matching_query)
        subqueries = configured or generic
        item_type = self._item_type(original, child_titles, subqueries)

        deterministic = [original]
        if normalize_query_key(prepared.matching_query) != normalize_query_key(original):
            deterministic.append(prepared.matching_query)
        deterministic.extend(alias_queries(original))
        for query in subqueries:
            deterministic.append(query)
            deterministic.extend(alias_queries(query))

        if item_type == SyllabusItemType.TASK and not configured:
            deterministic = []
            subqueries = ()
        elif item_type == SyllabusItemType.COLLECTION and child_titles:
            deterministic = [original, *configured, *child_titles]
            subqueries = (*configured, *child_titles)

        return QueryPlan(
            item_type=item_type,
            original_query=original,
            normalized_queries=_unique(tuple(deterministic)),
            subqueries=_unique(tuple(subqueries)),
            hierarchy_context=HierarchyContext(
                chapter=chapter,
                parent_title=parent_title,
                child_titles=child_titles,
                related_titles=related_titles,
            ),
        )

    @staticmethod
    def _item_type(value: str, child_titles: tuple[str, ...], subqueries: tuple[str, ...]) -> SyllabusItemType:
        if _TASK_MARKERS.search(value):
            return SyllabusItemType.TASK
        if child_titles or _COLLECTION_MARKERS.search(value):
            return SyllabusItemType.COLLECTION
        if len(subqueries) >= 2:
            return SyllabusItemType.COMPOSITE
        return SyllabusItemType.KNOWLEDGE

    @staticmethod
    def _split_subqueries(value: str) -> tuple[str, ...]:
        value = _COMPARISON_SUFFIX.sub("", value).strip()
        parts: list[str] = []
        for raw in _SPLIT_CONNECTORS.split(value):
            cleaned = raw.strip(" .。:：()（）")
            cleaned = _INSTRUCTION_FRAGMENTS.sub("", cleaned).strip(" .。:：()（）")
            if len(normalize_query_key(cleaned)) >= 2:
                parts.append(cleaned)
        parts = QueryPlanner._restore_shared_suffix(parts)
        return _unique(tuple(parts)) if len(parts) >= 2 else ()

    @staticmethod
    def _restore_shared_suffix(parts: list[str]) -> list[str]:
        if len(parts) < 2 or len(parts[-1]) < 4:
            return parts
        short_parts = parts[:-1]
        boundary = short_parts[0][-1] if short_parts and short_parts[0] else ""
        if (
            boundary not in _SHARED_SUFFIX_BOUNDARIES
            or any(len(part) > 6 or not part.endswith(boundary) for part in short_parts)
        ):
            return parts
        boundary_index = parts[-1].find(boundary)
        if boundary_index < 1:
            return parts
        shared_suffix = parts[-1][boundary_index + 1 :]
        if len(normalize_query_key(shared_suffix)) < 2:
            return parts
        return [f"{part}{shared_suffix}" for part in short_parts] + [parts[-1]]

    @staticmethod
    def _related_titles(entries: list[ParsedSyllabusItem], index: int) -> tuple[str, ...]:
        current = entries[index]
        requires_model = "模型" in current.title
        candidates: list[tuple[int, str]] = []
        for other_index, other in enumerate(entries):
            if other_index == index or other.chapter != current.chapter:
                continue
            distance = abs(other_index - index)
            if distance > 5:
                continue
            if requires_model and not re.search(r"理论|模型|原理", other.title):
                continue
            if re.search(r"理论|模型|方法|原理", other.title):
                candidates.append((distance, other.title))
        candidates.sort(key=lambda item: item[0])
        return _unique(tuple(title for _, title in candidates[:4]))


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
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
