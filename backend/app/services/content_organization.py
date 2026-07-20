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
_TOC_TRAILING_PAGE_REFERENCE = re.compile(r"\s*[/／]\s*\d+\s*$")
_BARE_CHAPTER_NUMBER = re.compile(r"^第\s*[一二三四五六七八九十百零〇\d]+\s*(?:章|章节|节|篇)$")
_ONLY_NUMBERING = re.compile(r"^[一二三四五六七八九十百零〇\d\s.、()（）-]+$")
_NUMBERED_CHAPTER = re.compile(r"^第\s*([一二三四五六七八九十百零〇\d]+)\s*章")
_CHAPTER_LINE = re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*章(?:\s+|[：:、.．\-—–])?.+")
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


@dataclass(frozen=True)
class SourceChapterBoundary:
    title: str
    start_position: int
    start_page: int
    chapter_number: int | None
    body_characters: int


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
    if _TOC_TRAILING_PAGE_REFERENCE.search(value):
        return False
    if _BARE_CHAPTER_NUMBER.fullmatch(value) or _ONLY_NUMBERING.fullmatch(value):
        return False
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", value))


def build_reliable_source_chapter_index(chunks: Iterable[Any]) -> dict[uuid.UUID, str]:
    """Map every chunk in a reliably detected textbook chapter range to its concrete chapter title.

    A boundary must be confirmed by a real body heading, not merely by a propagated
    title or a table-of-contents entry. When a document contains both a compact TOC
    chapter sequence and a later body sequence, the widest coherent body run wins.
    """
    ordered = sorted(chunks, key=lambda item: int(getattr(item, "position", 0)))
    candidates: list[SourceChapterBoundary] = []
    for chunk in ordered:
        title = _WHITESPACE.sub(" ", getattr(chunk, "chapter_title", None) or "").strip()
        if not is_displayable_source_chapter(title):
            continue
        if not _chunk_confirms_heading(title, chunk) or _chunk_looks_like_toc(chunk):
            continue
        candidates.append(SourceChapterBoundary(
            title=title,
            start_position=int(getattr(chunk, "position", 0)),
            start_page=int(getattr(chunk, "page_start", 0)),
            chapter_number=_chapter_number(title),
            body_characters=_heading_body_characters(title, chunk),
        ))
    boundaries = _best_boundary_run(candidates)
    if not boundaries:
        return {}

    result: dict[uuid.UUID, str] = {}
    boundary_index = 0
    current: SourceChapterBoundary | None = None
    for chunk in ordered:
        position = int(getattr(chunk, "position", 0))
        while boundary_index < len(boundaries) and position >= boundaries[boundary_index].start_position:
            current = boundaries[boundary_index]
            boundary_index += 1
        if current is None:
            continue
        chunk_id = getattr(chunk, "id", None) or getattr(chunk, "chunk_id", None)
        if isinstance(chunk_id, uuid.UUID):
            result[chunk_id] = current.title
    return result


def resolve_source_chapter_title(
    candidates: Iterable[Any],
    cited_ids: Iterable[uuid.UUID],
    reliable_chapter_by_chunk_id: dict[uuid.UUID, str] | None = None,
) -> str | None:
    """Resolve a concrete chapter from reliable body boundaries or direct heading evidence."""
    cited = Counter(cited_ids)
    candidate_list = list(candidates)
    if reliable_chapter_by_chunk_id:
        boundary_votes: Counter[str] = Counter()
        boundary_scores: dict[str, float] = defaultdict(float)
        boundary_pages: dict[str, int] = {}
        for candidate in candidate_list:
            count = cited.get(candidate.chunk_id, 0)
            title = reliable_chapter_by_chunk_id.get(candidate.chunk_id)
            if not count or not is_displayable_source_chapter(title):
                continue
            boundary_votes[title] += count
            boundary_scores[title] = max(boundary_scores[title], float(candidate.score))
            boundary_pages[title] = min(boundary_pages.get(title, int(candidate.page_start)), int(candidate.page_start))
        if boundary_votes:
            return max(
                boundary_votes,
                key=lambda title: (boundary_votes[title], boundary_scores[title], -boundary_pages[title]),
            )

    grouped: dict[str, list[Any]] = {}
    for candidate in candidate_list:
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
        for line in _candidate_text(candidate).splitlines()
        if line.strip()
    ]
    heading_indexes = [index for index, line in enumerate(lines[:3]) if normalize_content_title(line) == expected]
    if not heading_indexes:
        return False
    body = " ".join(line for index, line in enumerate(lines) if index not in heading_indexes)
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", body)) >= 20


def _candidate_text(candidate: Any) -> str:
    return str(getattr(candidate, "text", None) or getattr(candidate, "content", None) or "")


def _chunk_looks_like_toc(candidate: Any) -> bool:
    lines = [_WHITESPACE.sub(" ", line).strip() for line in _candidate_text(candidate).splitlines() if line.strip()]
    chapter_lines = {
        normalize_content_title(line)
        for line in lines
        if _CHAPTER_LINE.match(line)
    }
    return len(chapter_lines) >= 2


def _heading_body_characters(title: str, candidate: Any) -> int:
    expected = normalize_content_title(title)
    lines = [_WHITESPACE.sub(" ", line).strip() for line in _candidate_text(candidate).splitlines() if line.strip()]
    body = " ".join(line for line in lines if normalize_content_title(line) != expected)
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", body))


def _chapter_number(title: str) -> int | None:
    match = _NUMBERED_CHAPTER.match(title)
    if not match:
        return None
    value = match.group(1)
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "百" in value:
        hundreds, remainder = value.split("百", 1)
        base = digits.get(hundreds, 1) * 100
        return base + (_chinese_below_hundred(remainder, digits) if remainder else 0)
    return _chinese_below_hundred(value, digits)


def _chinese_below_hundred(value: str, digits: dict[str, int]) -> int | None:
    if not value:
        return 0
    if "十" in value:
        tens, ones = value.split("十", 1)
        return digits.get(tens, 1) * 10 + digits.get(ones, 0)
    if all(character in digits for character in value):
        number = 0
        for character in value:
            number = number * 10 + digits[character]
        return number
    return None


def _best_boundary_run(candidates: list[SourceChapterBoundary]) -> list[SourceChapterBoundary]:
    if not candidates:
        return []
    runs: list[list[SourceChapterBoundary]] = []
    current: list[SourceChapterBoundary] = []
    seen_titles: set[str] = set()
    previous_number: int | None = None
    for candidate in sorted(candidates, key=lambda item: item.start_position):
        normalized = normalize_content_title(candidate.title)
        resets_numbering = (
            previous_number is not None
            and candidate.chapter_number is not None
            and candidate.chapter_number <= previous_number
        )
        if current and (normalized in seen_titles or resets_numbering):
            runs.append(current)
            current = []
            seen_titles = set()
            previous_number = None
        current.append(candidate)
        seen_titles.add(normalized)
        if candidate.chapter_number is not None:
            previous_number = candidate.chapter_number
    if current:
        runs.append(current)

    def score(run: list[SourceChapterBoundary]) -> tuple[int, int, int, int]:
        unique = len({normalize_content_title(item.title) for item in run})
        span = max(item.start_page for item in run) - min(item.start_page for item in run)
        return unique, span, sum(item.body_characters for item in run), max(item.start_page for item in run)

    return max(runs, key=score)


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
