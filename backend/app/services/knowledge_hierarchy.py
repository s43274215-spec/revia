import re
from dataclasses import dataclass

from app.ai.schemas import GeneratedBulletPayload, GeneratedItemResult
from app.matching.schemas import CandidateChunk
from app.models.enums import ContentVersionKind
from app.services.content_organization import (
    CrossLevelBulletSnapshot,
    CrossLevelPointSnapshot,
    merge_version_content,
    normalize_content_title,
    plan_cross_level_dedup,
)


_COLLECTION_PATTERN = re.compile(r"(?:特征|特点|类型|原则|步骤|流程|因素|模块|构成|内容|方法)(?:是|为|包括|包含|如下|[：:]?\s*$)")


@dataclass(frozen=True)
class GeneratedRecord:
    syllabus_chapter: str | None
    syllabus_item: str
    parent_syllabus_item: str | None
    result: GeneratedItemResult
    candidates: list[CandidateChunk]


def normalize_title(value: str) -> str:
    return normalize_content_title(value)


def organize_generated_records(records: list[GeneratedRecord]) -> list[GeneratedRecord]:
    """Conservatively deduplicate and fold proven child topics into aggregate parents."""
    deduplicated = _deduplicate_knowledge_points(records)
    removed: set[int] = set()

    for parent_index, parent in enumerate(deduplicated):
        if parent_index in removed or not (
            _COLLECTION_PATTERN.search(parent.result.knowledge_point_title)
            or _COLLECTION_PATTERN.search(parent.syllabus_item)
        ):
            continue
        current_parent = parent
        for child_index, child in enumerate(deduplicated):
            if child_index <= parent_index or child_index in removed:
                continue
            if child.syllabus_chapter != parent.syllabus_chapter:
                continue
            explicit_parent = (
                child.parent_syllabus_item is not None
                and normalize_title(child.parent_syllabus_item) == normalize_title(parent.syllabus_item)
            )
            matching_parent_bullet = _find_bullet_index(
                current_parent.result.bullet_points,
                child.result.knowledge_point_title,
            )
            evidence_backed_parent = (
                matching_parent_bullet is not None
                and _source_overlap(current_parent.candidates, child.candidates) >= 0.5
            )
            if not explicit_parent and not evidence_backed_parent:
                continue
            current_parent = _fold_child(current_parent, child, matching_parent_bullet)
            removed.add(child_index)
        deduplicated[parent_index] = current_parent

    folded = [record for index, record in enumerate(deduplicated) if index not in removed]
    return _deduplicate_cross_level(folded)


def _deduplicate_cross_level(records: list[GeneratedRecord]) -> list[GeneratedRecord]:
    """Remove exact Bullet/KnowledgePoint title collisions within one generation scope."""
    result = list(records)
    snapshots = [CrossLevelPointSnapshot(
        scope=record.syllabus_chapter,
        title=record.result.knowledge_point_title,
        bullets=tuple(CrossLevelBulletSnapshot(
            title=bullet.title,
            version_contents=(bullet.original.content, bullet.recitation.content, bullet.keywords.content),
        ) for bullet in record.result.bullet_points),
    ) for record in result]
    removals: dict[int, set[int]] = {}
    for action in plan_cross_level_dedup(snapshots):
        owner = result[action.owner_index]
        incoming = owner.result.bullet_points[action.bullet_index]
        if action.merge_into_target:
            result[action.target_index] = _merge_generated_bullet_into_record(
                result[action.target_index], incoming, owner.candidates,
            )
        removals.setdefault(action.owner_index, set()).add(action.bullet_index)
    for owner_index, bullet_indexes in removals.items():
        owner = result[owner_index]
        result[owner_index] = GeneratedRecord(
            syllabus_chapter=owner.syllabus_chapter,
            syllabus_item=owner.syllabus_item,
            parent_syllabus_item=owner.parent_syllabus_item,
            result=owner.result.model_copy(update={
                "bullet_points": [
                    bullet for index, bullet in enumerate(owner.result.bullet_points)
                    if index not in bullet_indexes
                ],
            }),
            candidates=owner.candidates,
        )
    return [record for record in result if record.result.bullet_points]


def _merge_generated_bullet_into_record(
    record: GeneratedRecord,
    incoming: GeneratedBulletPayload,
    incoming_candidates: list[CandidateChunk],
) -> GeneratedRecord:
    bullets = list(record.result.bullet_points)
    target_index = max(range(len(bullets)), key=lambda index: _generated_content_overlap(bullets[index], incoming))
    bullets[target_index] = _merge_generated_bullets(bullets[target_index], incoming)
    return GeneratedRecord(
        syllabus_chapter=record.syllabus_chapter,
        syllabus_item=record.syllabus_item,
        parent_syllabus_item=record.parent_syllabus_item,
        result=record.result.model_copy(update={"bullet_points": bullets}),
        candidates=_merge_candidates(record.candidates, incoming_candidates),
    )


def _generated_content_overlap(first: GeneratedBulletPayload, second: GeneratedBulletPayload) -> int:
    first_terms = set(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", first.original.content.casefold()))
    second_terms = set(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", second.original.content.casefold()))
    return len(first_terms & second_terms)


def _merge_generated_bullets(
    preferred: GeneratedBulletPayload,
    additional: GeneratedBulletPayload,
) -> GeneratedBulletPayload:
    def merged(kind: ContentVersionKind):
        first = getattr(preferred, kind.value)
        second = getattr(additional, kind.value)
        return first.model_copy(update={"content": merge_version_content(kind, first.content, second.content)})

    return preferred.model_copy(update={
        "original": merged(ContentVersionKind.ORIGINAL),
        "recitation": merged(ContentVersionKind.RECITATION),
        "keywords": merged(ContentVersionKind.KEYWORDS),
        "source_chunk_ids": list(dict.fromkeys([*preferred.source_chunk_ids, *additional.source_chunk_ids])),
        "source_pages": sorted(set([*preferred.source_pages, *additional.source_pages])),
    })


def _deduplicate_knowledge_points(records: list[GeneratedRecord]) -> list[GeneratedRecord]:
    unique: list[GeneratedRecord] = []
    positions: dict[tuple[str | None, str], int] = {}
    for record in records:
        key = (record.syllabus_chapter, normalize_title(record.result.knowledge_point_title))
        existing_index = positions.get(key)
        if existing_index is None:
            positions[key] = len(unique)
            unique.append(record)
            continue
        unique[existing_index] = _merge_duplicate_record(unique[existing_index], record)
    return unique


def _merge_duplicate_record(primary: GeneratedRecord, duplicate: GeneratedRecord) -> GeneratedRecord:
    bullets = list(primary.result.bullet_points)
    for incoming in duplicate.result.bullet_points:
        existing_index = _find_bullet_index(bullets, incoming.title)
        if existing_index is None:
            bullets.append(incoming)
        else:
            bullets[existing_index] = _merge_bullet_sources(bullets[existing_index], incoming)
    return GeneratedRecord(
        syllabus_chapter=primary.syllabus_chapter,
        syllabus_item=primary.syllabus_item,
        parent_syllabus_item=primary.parent_syllabus_item or duplicate.parent_syllabus_item,
        result=primary.result.model_copy(update={"bullet_points": bullets}),
        candidates=_merge_candidates(primary.candidates, duplicate.candidates),
    )


def _fold_child(
    parent: GeneratedRecord,
    child: GeneratedRecord,
    matching_parent_bullet: int | None,
) -> GeneratedRecord:
    bullets = list(parent.result.bullet_points)
    child_bullets = _retitle_child_bullets(child)
    if matching_parent_bullet is not None:
        child_bullets[0] = _merge_bullet_sources(
            child_bullets[0],
            bullets[matching_parent_bullet],
        )
        bullets[matching_parent_bullet] = child_bullets[0]
        bullets.extend(child_bullets[1:])
    else:
        for bullet in child_bullets:
            existing_index = _find_bullet_index(bullets, bullet.title)
            if existing_index is None:
                bullets.append(bullet)
            else:
                bullets[existing_index] = _merge_bullet_sources(bullets[existing_index], bullet)
    return GeneratedRecord(
        syllabus_chapter=parent.syllabus_chapter,
        syllabus_item=parent.syllabus_item,
        parent_syllabus_item=parent.parent_syllabus_item,
        result=parent.result.model_copy(update={"bullet_points": bullets}),
        candidates=_merge_candidates(parent.candidates, child.candidates),
    )


def _retitle_child_bullets(child: GeneratedRecord) -> list[GeneratedBulletPayload]:
    child_title = child.result.knowledge_point_title
    retitled: list[GeneratedBulletPayload] = []
    for index, bullet in enumerate(child.result.bullet_points):
        title = child_title if index == 0 else _joined_title(child_title, bullet.title)
        retitled.append(bullet.model_copy(update={
            "title": title,
            "original": bullet.original.model_copy(update={"title": title}),
            "recitation": bullet.recitation.model_copy(update={"title": title}),
            "keywords": bullet.keywords.model_copy(update={"title": title}),
        }))
    return retitled


def _joined_title(parent: str, child: str) -> str:
    if normalize_title(parent) == normalize_title(child):
        return parent
    value = f"{parent}·{child}"
    return value if len(value) <= 25 else child


def _merge_bullet_sources(
    preferred: GeneratedBulletPayload,
    additional: GeneratedBulletPayload,
) -> GeneratedBulletPayload:
    source_ids = list(dict.fromkeys([*preferred.source_chunk_ids, *additional.source_chunk_ids]))
    source_pages = sorted(set([*preferred.source_pages, *additional.source_pages]))
    return preferred.model_copy(update={
        "source_chunk_ids": source_ids,
        "source_pages": source_pages,
    })


def _find_bullet_index(bullets: list[GeneratedBulletPayload], title: str) -> int | None:
    normalized = normalize_title(title)
    return next(
        (index for index, bullet in enumerate(bullets) if normalize_title(bullet.title) == normalized),
        None,
    )


def _source_overlap(first: list[CandidateChunk], second: list[CandidateChunk]) -> float:
    first_ids = {candidate.chunk_id for candidate in first}
    second_ids = {candidate.chunk_id for candidate in second}
    if not first_ids or not second_ids:
        return 0.0
    return len(first_ids & second_ids) / min(len(first_ids), len(second_ids))


def _merge_candidates(first: list[CandidateChunk], second: list[CandidateChunk]) -> list[CandidateChunk]:
    return list({candidate.chunk_id: candidate for candidate in [*first, *second]}.values())
