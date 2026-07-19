"""Production-safe read-only diagnostic for content organization.

This script executes SELECT statements only and exits before project queries unless
the connected PostgreSQL role is exactly ``revia_audit_ro``.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

backend_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_root))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.ai.schemas import GeneratedItemResult
from app.matching.service import MatchingService
from app.matching.schemas import CandidateChunk
from app.models.document import TextChunk
from app.services.content_organization import canonical_learning_material, normalize_content_title, resolve_source_chapter_title
from app.syllabus.parser import SyllabusParser


PROJECT_ID = uuid.UUID("03ffb29f-b450-4fc2-9cb2-d6525cb9aaf5")
REQUIRED_ROLE = "revia_audit_ro"
FOCUS_ITEMS = {
    "各模块核心考点": "各模块核心考点",
    "经典核心理论": "经典核心理论",
    "X-Y 理论": "X-Y 理论",
    "双因素理论": "双因素理论",
    "行业发展简史": "行业发展简史",
    "德尔菲法、经验判断法": "德尔菲法、经验判断法",
    "内外部供给计算逻辑": "内部供给、外部供给",
    "工作说明书与6W1H": "工作说明书",
    "问卷法等基础方法": "问卷法等基础方法",
    "模型案例/论述分析": "结合模型做案例",
    "招募、甄选、人员配置内涵": "招募、甄选、人员配置",
    "招聘相关计算": "相关计算",
}


def _database_url() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise SystemExit("DATABASE_URL is not configured")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        raise SystemExit("Dry-run requires PostgreSQL")
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _state(row: dict[str, object], match, evidence: list[CandidateChunk]) -> dict[str, object]:
    candidates = evidence
    content_matched = bool(match.matched and candidates)
    valid = False
    if row["result_payload"]:
        try:
            GeneratedItemResult.model_validate(row["result_payload"])
            valid = row["status"] == "succeeded"
        except ValueError:
            valid = False
    cited_ids = [candidate.chunk_id for candidate in candidates]
    chapter = resolve_source_chapter_title(candidates, cited_ids) if content_matched else None
    chapter_evidence = [
        {
            "page_start": candidate.page_start,
            "page_end": candidate.page_end,
            "chapter": candidate.chapter,
            "text_head": " | ".join(line.strip() for line in candidate.text.splitlines()[:3])[:240],
        }
        for candidate in candidates
        if chapter is not None and candidate.chapter == chapter and candidate.chunk_id in set(cited_ids)
    ]
    return {
        "position": row["position"],
        "syllabus_item": row["syllabus_item"],
        "content_matched": content_matched,
        "chapter_resolved": chapter is not None,
        "chapter": chapter,
        "chapter_evidence": chapter_evidence,
        "generation_valid": valid,
        "failure_type": row["failure_type"],
        "matching_reason": match.unmatched_reason_category,
        "top_scores": match.query_top_scores,
    }


def _current_matches(conn):
    syllabus_text = conn.execute(text("""
        SELECT text FROM syllabi WHERE project_id = CAST(:project_id AS uuid)
    """), {"project_id": str(PROJECT_ID)}).scalar_one()
    chunk_rows = conn.execute(text("""
        SELECT tc.id, tc.parsed_document_id, tc.position, tc.page_start, tc.page_end,
               tc.chapter_title, tc.section_title, tc.content
        FROM text_chunks tc
        JOIN parsed_documents pd ON pd.id = tc.parsed_document_id
        JOIN documents d ON d.id = pd.document_id
        WHERE d.project_id = CAST(:project_id AS uuid) AND d.kind = 'COURSE_MATERIAL'
        ORDER BY tc.position
    """), {"project_id": str(PROJECT_ID)}).mappings().all()
    chunks = [TextChunk(
        id=row["id"], parsed_document_id=row["parsed_document_id"], position=row["position"],
        page_start=row["page_start"], page_end=row["page_end"], chapter_title=row["chapter_title"],
        section_title=row["section_title"], content=row["content"],
    ) for row in chunk_rows]
    entries = SyllabusParser().flatten_hierarchy(syllabus_text or "")
    matching = MatchingService(threshold=0.35, max_candidates=6)
    plans = matching.plan_items(entries)
    matches = [matching.match_plan(plan=plan, chunks=chunks) for plan in plans]
    matches = matching.resolve_dependent_matches(plans, matches)
    evidence = [matching.select_generation_evidence(match=match, chunks=chunks) if match.matched else [] for match in matches]
    return entries, matches, evidence


def _load_material(conn) -> tuple[list[SimpleNamespace], list[dict[str, object]]]:
    rows = conn.execute(text("""
        SELECT c.id AS chapter_id, c.title AS chapter_title, c.position AS chapter_position,
               kp.id AS point_id, kp.title AS point_title, kp.position AS point_position,
               bp.id AS bullet_id, bp.position AS bullet_position,
               cv.id AS version_id, cv.kind AS version_kind, cv.title AS version_title, cv.content,
               bps.text_chunk_id, bps.page_start, bps.page_end
        FROM chapters c
        JOIN knowledge_points kp ON kp.chapter_id = c.id
        JOIN bullet_points bp ON bp.knowledge_point_id = kp.id
        JOIN content_versions cv ON cv.bullet_point_id = bp.id
        LEFT JOIN bullet_point_sources bps ON bps.bullet_point_id = bp.id
        WHERE c.project_id = CAST(:project_id AS uuid)
        ORDER BY c.position, kp.position, bp.position, cv.kind, bps.text_chunk_id
    """), {"project_id": str(PROJECT_ID)}).mappings().all()

    chapters: dict[uuid.UUID, SimpleNamespace] = {}
    points: dict[uuid.UUID, SimpleNamespace] = {}
    bullets: dict[uuid.UUID, SimpleNamespace] = {}
    versions_seen: set[uuid.UUID] = set()
    sources_seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    collision_rows: list[dict[str, object]] = []
    for row in rows:
        chapter = chapters.setdefault(row["chapter_id"], SimpleNamespace(
            id=row["chapter_id"], title=row["chapter_title"], position=row["chapter_position"], knowledge_points=[],
        ))
        if row["point_id"] not in points:
            points[row["point_id"]] = SimpleNamespace(
                id=row["point_id"], title=row["point_title"], position=row["point_position"], bullet_points=[],
            )
            chapter.knowledge_points.append(points[row["point_id"]])
        point = points[row["point_id"]]
        if row["bullet_id"] not in bullets:
            bullets[row["bullet_id"]] = SimpleNamespace(
                id=row["bullet_id"], position=row["bullet_position"], versions=[], sources=[],
            )
            point.bullet_points.append(bullets[row["bullet_id"]])
        bullet = bullets[row["bullet_id"]]
        if row["version_id"] not in versions_seen:
            versions_seen.add(row["version_id"])
            bullet.versions.append(SimpleNamespace(
                id=row["version_id"], kind=str(row["version_kind"]).lower(), title=row["version_title"], content=row["content"],
            ))
        source_key = (row["bullet_id"], row["text_chunk_id"])
        if row["text_chunk_id"] is not None and source_key not in sources_seen:
            sources_seen.add(source_key)
            bullet.sources.append(SimpleNamespace(
                text_chunk_id=row["text_chunk_id"], page_start=row["page_start"], page_end=row["page_end"],
            ))

    for chapter in chapters.values():
        title_index = defaultdict(list)
        for point in chapter.knowledge_points:
            title_index[normalize_content_title(point.title)].append(point)
        for owner in chapter.knowledge_points:
            for bullet in owner.bullet_points:
                bullet_title = next((version.title for version in bullet.versions if version.title), "")
                targets = [point for point in title_index[normalize_content_title(bullet_title)] if point.id != owner.id]
                if targets:
                    collision_rows.append({
                        "chapter": chapter.title,
                        "broad_point": owner.title,
                        "bullet": bullet_title,
                        "standalone_points": [point.title for point in targets],
                    })
    return list(chapters.values()), collision_rows


def main() -> None:
    engine = create_engine(_database_url())
    with engine.connect() as conn:
        role = conn.execute(text("SELECT current_user")).scalar_one()
        print(json.dumps({"database_role": role}, ensure_ascii=False))
        if role != REQUIRED_ROLE:
            raise SystemExit(f"Refusing dry-run: expected {REQUIRED_ROLE}, got {role}")

        job = conn.execute(text("""
            SELECT id, status, total_items
            FROM generation_jobs
            WHERE project_id = CAST(:project_id AS uuid)
            ORDER BY created_at DESC
            LIMIT 1
        """), {"project_id": str(PROJECT_ID)}).mappings().one()
        item_rows = conn.execute(text("""
            SELECT position, syllabus_item, status, failure_type, result_payload, candidates_payload
            FROM generation_job_items
            WHERE job_id = :job_id
            ORDER BY position
        """), {"job_id": job["id"]}).mappings().all()
        entries, matches, evidence = _current_matches(conn)
        if len(entries) != len(item_rows):
            raise RuntimeError(f"Syllabus/checkpoint count mismatch: {len(entries)} != {len(item_rows)}")
        states = [_state(dict(row), matches[index], evidence[index]) for index, row in enumerate(item_rows)]
        counts = {
            "total": len(states),
            "content_matched_true": sum(bool(item["content_matched"]) for item in states),
            "content_matched_false": sum(not item["content_matched"] for item in states),
            "chapter_resolved_true": sum(bool(item["chapter_resolved"]) for item in states),
            "chapter_resolved_false": sum(not item["chapter_resolved"] for item in states),
            "generation_valid_true": sum(bool(item["generation_valid"]) for item in states),
            "schema_failure": sum(item["failure_type"] == "schema_validation" for item in states),
        }
        focus = {
            label: [item for item in states if normalize_content_title(needle) in normalize_content_title(str(item["syllabus_item"]))]
            for label, needle in FOCUS_ITEMS.items()
        }

        chapters, collisions = _load_material(conn)
        canonical = canonical_learning_material(PROJECT_ID, chapters)
        target = [
            {
                "chapter": chapter.title,
                "knowledge_point": point.title,
                "bullet_titles": [next((v.title for v in bullet.versions if v.title), "") for bullet in point.bullet_points],
            }
            for chapter in canonical.chapters
            for point in chapter.knowledge_points
            if normalize_content_title(point.title) == normalize_content_title("人力资源的特征")
        ]
        print(json.dumps({
            "project_id": str(PROJECT_ID),
            "latest_job": {"id": str(job["id"]), "status": job["status"], "declared_total": job["total_items"]},
            "counts": counts,
            "items": states,
            "focus_items": focus,
            "cross_level_collisions_before": collisions,
            "cross_level_collision_count_before": len(collisions),
            "canonical_target": target,
        }, ensure_ascii=False, indent=2, default=str))
    engine.dispose()


if __name__ == "__main__":
    main()
