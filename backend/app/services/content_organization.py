from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Hashable

from app.models.content import Chapter
from app.models.enums import ContentVersionKind
from app.schemas.content import (
    BulletPointRead,
    BulletPointSourceRead,
    ChapterRead,
    ContentVersionRead,
    KnowledgePointRead,
    LearningMaterialRead,
)


INTERNAL_UNRESOLVED_CHAPTER = "__revia_unresolved_source_chapter__"

_PUNCTUATION_TRANSLATION = str.maketrans({
    "，": ",", "。": ".", "：": ":", "；": ";", "！": "!", "？": "?",
    "（": "(", "）": ")", "【": "[", "】": "]", "｛": "{", "｝": "}",
    "“": '"', "”": '"', "‘": "'", "’": "'", "、": ",", "．": ".",
    "－": "-", "—": "-", "–": "-", "／": "/", "＼": "\\", "｜": "|",
})
_NUMBER_PREFIX = re.compile(r"^\s*(?:\(?\d+\)?\s*[.、,:：;；)）-]|第\s*\d+\s*[章节篇]\s*)\s*")
_WHITESPACE = re.compile(r"\s+")
_PAGE_RANGE = re.compile(r"(?:^|[·•|｜])?\s*第?\s*\d+\s*(?:[-~至]\s*\d+\s*)?页\s*$", re.IGNORECASE)
_FILE_PAGE_RANGE = re.compile(r"\.(?:pdf|docx?|pptx?|txt)\s*(?:[·•|｜-]\s*)?第?\s*\d+", re.IGNORECASE)
_BARE_CHAPTER_NUMBER = re.compile(r"^第\s*[一二三四五六七八九十百零〇\d]+\s*(?:章|章节|节|篇)$")
_ONLY_NUMBERING = re.compile(r"^[一二三四五六七八九十百零〇\d\s.、()（）-]+$")
_HIDDEN_CHAPTERS = {
    "未分章", "未归类内容", "未分类", "未分类内容", "其他", "其它",
    "题纲父标题", "提纲父标题", "大纲父标题", "目录", "课程目录", "contents",
    INTERNAL_UNRESOLVED_CHAPTER,
}


@dataclass(frozen=True)
class CrossLevelBulletSnapshot:
    title: str
    version_contents: tuple[str, str, str]


@dataclass(frozen=True)
class CrossLevelPointSnapshot:
    scope: Hashable
    title: str
    bullets: tuple[CrossLevelBulletSnapshot, ...]


@dataclass(frozen=True)
class CrossLevelCollisionAction:
    owner_index: int
    bullet_index: int
    target_index: int
    merge_into_target: bool


def normalize_content_title(value: str) -> str:
    """Normalize exact-title identity without performing semantic or fuzzy matching."""
    normalized = value.translate(_PUNCTUATION_TRANSLATION)
    normalized = _NUMBER_PREFIX.sub("", normalized, count=1)
    return _WHITESPACE.sub(" ", normalized).strip().casefold()


# Compatibility for existing hierarchy helpers and tests.
normalize_title = normalize_content_title


def plan_cross_level_dedup(points: list[CrossLevelPointSnapshot]) -> list[CrossLevelCollisionAction]:
    """Plan strict Bullet/KnowledgePoint collision handling for any content representation."""
    title_index: dict[tuple[Hashable, str], list[int]] = {}
    for index, point in enumerate(points):
        key = (point.scope, normalize_content_title(point.title))
        if key[1]:
            title_index.setdefault(key, []).append(index)

    actions: list[CrossLevelCollisionAction] = []
    for owner_index, owner in enumerate(points):
        for bullet_index, bullet in enumerate(owner.bullets):
            key = (owner.scope, normalize_content_title(bullet.title))
            candidates = [index for index in title_index.get(key, []) if index != owner_index]
            if not candidates:
                continue
            target_index = max(candidates, key=lambda index: _snapshot_point_completeness(points[index]))
            actions.append(CrossLevelCollisionAction(
                owner_index=owner_index,
                bullet_index=bullet_index,
                target_index=target_index,
                merge_into_target=_snapshot_bullet_completeness(bullet) > _snapshot_point_completeness(points[target_index]),
            ))
    return actions


def _snapshot_point_completeness(point: CrossLevelPointSnapshot) -> tuple[int, int, int, int]:
    contents = [content.strip() for bullet in point.bullets for content in bullet.version_contents]
    headings = {normalize_content_title(bullet.title) for bullet in point.bullets if bullet.title.strip()}
    return len(point.bullets), len(headings), sum(bool(value) for value in contents), sum(map(len, contents))


def _snapshot_bullet_completeness(bullet: CrossLevelBulletSnapshot) -> tuple[int, int, int, int]:
    contents = [content.strip() for content in bullet.version_contents]
    return 1, int(bool(bullet.title.strip())), sum(bool(value) for value in contents), sum(map(len, contents))


def is_displayable_source_chapter(
    title: str | None,
    metadata: dict[str, object] | None = None,
) -> bool:
    if metadata is not None:
        if metadata.get("chapter_resolved") is False:
            return False
        if metadata.get("source") in {"syllabus_parent", "template_header", "toc_propagation"}:
            return False
    value = _WHITESPACE.sub(" ", title or "").strip()
    if not value or value.casefold() in _HIDDEN_CHAPTERS or value.startswith("__revia_"):
        return False
    if _PAGE_RANGE.search(value) or _FILE_PAGE_RANGE.search(value):
        return False
    if _BARE_CHAPTER_NUMBER.fullmatch(value) or _ONLY_NUMBERING.fullmatch(value):
        return False
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", value))


def resolve_source_chapter_title(candidates: Iterable[Any], cited_ids: Iterable[uuid.UUID]) -> str | None:
    """Resolve only a concrete chapter heading confirmed by its cited chunk text."""
    cited = Counter(cited_ids)
    grouped: dict[str, list[Any]] = {}
    for candidate in candidates:
        chapter = getattr(candidate, "chapter", None)
        if candidate.chunk_id not in cited or not is_displayable_source_chapter(chapter):
            continue
        grouped.setdefault(_WHITESPACE.sub(" ", chapter).strip(), []).append(candidate)
    reliable: dict[str, tuple[int, float, int]] = {}
    for title, matches in grouped.items():
        if not any(_chunk_confirms_heading(title, candidate) for candidate in matches):
            continue
        reliable[title] = (
            sum(cited[candidate.chunk_id] for candidate in matches),
            max(float(candidate.score) for candidate in matches),
            min(int(candidate.page_start) for candidate in matches),
        )
    if not reliable:
        return None
    return max(reliable, key=lambda title: (reliable[title][0], reliable[title][1], -reliable[title][2]))


def _chunk_confirms_heading(title: str, candidate: Any) -> bool:
    # A heading propagated across a very large chunk is not a reliable chapter boundary.
    if int(candidate.page_end) - int(candidate.page_start) > 2:
        return False
    expected = normalize_content_title(title)
    lines = [
        _WHITESPACE.sub(" ", line).strip()
        for line in getattr(candidate, "text", "").splitlines()
        if line.strip()
    ]
    heading_indexes = [index for index, line in enumerate(lines[:3]) if normalize_content_title(line) == expected]
    if not heading_indexes:
        return False
    body = " ".join(line for index, line in enumerate(lines) if index not in heading_indexes)
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", body)) >= 20


def canonical_learning_material(project_id: uuid.UUID, chapters: Iterable[Chapter]) -> LearningMaterialRead:
    """Build a read-only canonical DTO; ORM objects are never mutated."""
    canonical_chapters: list[ChapterRead] = []
    for chapter in sorted(chapters, key=lambda item: item.position):
        points = [_point_dto(point) for point in sorted(chapter.knowledge_points, key=lambda item: item.position)]
        points = _remove_cross_level_collisions(points)
        displayable = is_displayable_source_chapter(chapter.title)
        canonical_chapters.append(ChapterRead(
            id=chapter.id,
            title=chapter.title if displayable else None,
            position=chapter.position,
            chapter_resolved=displayable,
            knowledge_points=points,
        ))
    return LearningMaterialRead(project_id=project_id, chapters=canonical_chapters)


def _point_dto(point: object) -> KnowledgePointRead:
    return KnowledgePointRead(
        id=point.id,
        title=point.title,
        position=point.position,
        bullet_points=[
            BulletPointRead(
                id=bullet.id,
                position=bullet.position,
                versions=[ContentVersionRead.model_validate(version) for version in bullet.versions],
                sources=[BulletPointSourceRead.model_validate(source) for source in bullet.sources],
            )
            for bullet in sorted(point.bullet_points, key=lambda item: item.position)
        ],
    )


def _remove_cross_level_collisions(points: list[KnowledgePointRead]) -> list[KnowledgePointRead]:
    result = [point.model_copy(deep=True) for point in points]
    snapshots = [CrossLevelPointSnapshot(
        scope="chapter",
        title=point.title,
        bullets=tuple(CrossLevelBulletSnapshot(
            title=_bullet_title(bullet),
            version_contents=_version_contents(bullet),
        ) for bullet in point.bullet_points),
    ) for point in result]
    actions = plan_cross_level_dedup(snapshots)
    removals: dict[int, set[int]] = defaultdict(set)
    for action in actions:
        incoming = result[action.owner_index].bullet_points[action.bullet_index]
        if action.merge_into_target:
            result[action.target_index] = _merge_bullet_into_point(result[action.target_index], incoming)
        removals[action.owner_index].add(action.bullet_index)
    for owner_index, bullet_indexes in removals.items():
        result[owner_index] = result[owner_index].model_copy(update={
            "bullet_points": [
                bullet for index, bullet in enumerate(result[owner_index].bullet_points)
                if index not in bullet_indexes
            ],
        })
    return [point for point in result if point.bullet_points]


def _bullet_title(bullet: BulletPointRead) -> str:
    return next((version.title for version in bullet.versions if version.title.strip()), "")


def _version_contents(bullet: BulletPointRead) -> tuple[str, str, str]:
    by_kind = {version.kind: version.content for version in bullet.versions}
    return (
        by_kind.get(ContentVersionKind.ORIGINAL, ""),
        by_kind.get(ContentVersionKind.RECITATION, ""),
        by_kind.get(ContentVersionKind.KEYWORDS, ""),
    )


def _merge_bullet_into_point(point: KnowledgePointRead, incoming: BulletPointRead) -> KnowledgePointRead:
    if not point.bullet_points:
        return point.model_copy(update={"bullet_points": [incoming.model_copy(update={"position": 0})]})
    target_index = max(
        range(len(point.bullet_points)),
        key=lambda index: _content_overlap(point.bullet_points[index], incoming),
    )
    bullets = list(point.bullet_points)
    bullets[target_index] = _merge_bullet(bullets[target_index], incoming)
    return point.model_copy(update={"bullet_points": bullets})


def _content_overlap(first: BulletPointRead, second: BulletPointRead) -> int:
    first_text = " ".join(version.content for version in first.versions)
    second_text = " ".join(version.content for version in second.versions)
    first_terms = set(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", first_text.casefold()))
    second_terms = set(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", second_text.casefold()))
    return len(first_terms & second_terms)


def _merge_bullet(preferred: BulletPointRead, additional: BulletPointRead) -> BulletPointRead:
    versions: list[ContentVersionRead] = []
    additional_by_kind = {version.kind: version for version in additional.versions}
    for version in preferred.versions:
        incoming = additional_by_kind.get(version.kind)
        versions.append(version if incoming is None else version.model_copy(update={
            "content": merge_version_content(version.kind, version.content, incoming.content),
        }))
    existing_kinds = {version.kind for version in versions}
    versions.extend(version for version in additional.versions if version.kind not in existing_kinds)
    sources = _unique_sources([*preferred.sources, *additional.sources])
    return preferred.model_copy(update={"versions": versions, "sources": sources})


def merge_version_content(kind: ContentVersionKind, preferred: str, additional: str) -> str:
    if kind == ContentVersionKind.KEYWORDS:
        pieces = re.split(r"[\n、，,；;]+", f"{preferred}、{additional}")
        separator = "、"
    else:
        pieces = re.split(r"\n\s*\n+", f"{preferred}\n\n{additional}")
        separator = "\n\n"
    unique: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        cleaned = _WHITESPACE.sub(" ", piece).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            unique.append(cleaned)
    return separator.join(unique)


def _unique_sources(sources: list[BulletPointSourceRead]) -> list[BulletPointSourceRead]:
    result: list[BulletPointSourceRead] = []
    seen: set[uuid.UUID] = set()
    for source in sources:
        if source.text_chunk_id not in seen:
            seen.add(source.text_chunk_id)
            result.append(source)
    return result
