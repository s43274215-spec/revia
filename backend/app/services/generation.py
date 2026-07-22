import asyncio
import uuid
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.schemas import GeneratedItemResult
from app.ai.service import AIService, ItemGenerationRequest
from app.ai.validation import AIOutputValidationError
from app.matching.schemas import CandidateChunk, ItemMatch
from app.matching.aliases import normalize_query_key
from app.matching.query_planning import QueryPlan, SyllabusItemType
from app.matching.service import MatchingService
from app.models.content import BulletPoint, BulletPointSource, Chapter, ContentVersion, KnowledgePoint
from app.models.document import ParsedDocument, TextChunk
from app.models.enums import ContentVersionKind, DocumentKind, GenerationItemStatus, GenerationStatus, ProjectStatus
from app.models.project import Document, GenerationJob, GenerationJobItem, Project
from app.services.knowledge_hierarchy import GeneratedRecord, organize_generated_records
from app.services.content_organization import (
    INTERNAL_UNRESOLVED_CHAPTER,
    build_reliable_source_chapter_index,
    resolve_source_chapter_title,
)
from app.syllabus.parser import SyllabusParser


class ProjectNotFoundError(LookupError):
    pass


_ACTIVE_JOB_STATUSES = (
    GenerationStatus.PENDING,
    GenerationStatus.PARSING,
    GenerationStatus.MATCHING,
    GenerationStatus.GENERATING,
    GenerationStatus.VALIDATING,
)
_TERMINAL_JOB_STATUSES = (
    GenerationStatus.COMPLETED,
    GenerationStatus.PARTIAL_FAILED,
    GenerationStatus.FAILED,
)
_TERMINAL_ITEM_STATUSES = (
    GenerationItemStatus.SUCCEEDED.value,
    GenerationItemStatus.FAILED.value,
)


def find_generation_job(
    db: Session,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
) -> GenerationJob | None:
    return db.scalar(
        select(GenerationJob)
        .join(Project, GenerationJob.project_id == Project.id)
        .where(
            GenerationJob.id == job_id,
            GenerationJob.project_id == project_id,
            Project.workspace_id == workspace_id,
        )
    )


@dataclass(frozen=True)
class PreparedGeneration:
    job: GenerationJob
    should_process: bool


class GenerationWorkflowService:
    def __init__(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        ai_service: AIService | None,
        matching_service: MatchingService | None,
        provider_name: str,
        syllabus_parser: SyllabusParser | None = None,
        stale_after_seconds: int = 1200,
    ) -> None:
        self._db = db
        self._workspace_id = workspace_id
        self._ai_service = ai_service
        self._matching = matching_service
        self._provider_name = provider_name
        self._syllabus_parser = syllabus_parser or SyllabusParser()
        self._stale_after = timedelta(seconds=stale_after_seconds)

    async def start(self, project_id: uuid.UUID, *, regenerate: bool = False) -> GenerationJob:
        prepared = self.prepare(project_id, regenerate=regenerate)
        if not prepared.should_process:
            return prepared.job
        return await self.process(project_id, prepared.job.id)

    def prepare(self, project_id: uuid.UUID, *, regenerate: bool = False) -> PreparedGeneration:
        project = self._project(project_id, lock=True)
        if project is None:
            raise ProjectNotFoundError(f"Project {project_id} was not found")

        active_jobs = self._active_jobs(project_id)
        for duplicate in active_jobs[1:]:
            self._mark_failed(
                duplicate,
                project,
                "superseded_concurrent_generation: another active generation job owns this project",
            )
        active = active_jobs[0] if active_jobs else None
        if active is not None:
            self._recover_job_locked(active, project)
            if active.status in _ACTIVE_JOB_STATUSES:
                project.status = ProjectStatus.PROCESSING
                self._db.commit()
                self._db.refresh(active)
                return PreparedGeneration(job=active, should_process=False)

        existing = self._latest_reusable_job(project_id)
        if existing is not None and not regenerate:
            self._db.commit()
            self._db.refresh(existing)
            return PreparedGeneration(job=existing, should_process=False)

        syllabus_text = project.syllabus.text if project.syllabus and project.syllabus.text else ""
        syllabus_items = self._syllabus_parser.flatten_hierarchy(syllabus_text)
        retry_items = self._retryable_partial_items(existing, syllabus_items) if regenerate else None
        now = datetime.now(UTC)
        reusable_success_count = sum(
            item.status == GenerationItemStatus.SUCCEEDED.value
            and item.result_payload is not None
            and item.candidates_payload is not None
            for item in (retry_items or [])
        )
        progress = (
            min(95, 20 + int(75 * reusable_success_count / max(len(syllabus_items), 1)))
            if reusable_success_count
            else 0
        )
        job = GenerationJob(
            project_id=project.id,
            status=GenerationStatus.PENDING,
            provider=self._provider_name,
            progress=progress,
            processed_items=reusable_success_count,
            total_items=len(syllabus_items),
            item_failures=[],
            status_history=[GenerationStatus.PENDING.value],
            successful_items=reusable_success_count,
            failed_items=0,
            started_at=now,
            last_activity_at=now,
        )
        project.status = ProjectStatus.PROCESSING
        self._db.add(job)
        self._db.flush()
        checkpoints: list[GenerationJobItem] = []
        for index, entry in enumerate(syllabus_items):
            previous = retry_items[index] if retry_items is not None else None
            reusable = bool(
                previous is not None
                and previous.status == GenerationItemStatus.SUCCEEDED.value
                and previous.result_payload is not None
                and previous.candidates_payload is not None
            )
            checkpoints.append(GenerationJobItem(
                job_id=job.id,
                position=index,
                syllabus_chapter=entry.chapter,
                syllabus_item=entry.title,
                parent_syllabus_item=entry.parent_title,
                status=(
                    GenerationItemStatus.SUCCEEDED.value
                    if reusable
                    else GenerationItemStatus.PENDING.value
                ),
                result_payload=deepcopy(previous.result_payload) if reusable else None,
                candidates_payload=(
                    deepcopy(previous.candidates_payload)
                    if previous is not None and previous.candidates_payload is not None
                    else None
                ),
                failure_type=previous.failure_type if reusable else None,
                error_message=previous.error_message if reusable else None,
                started_at=now if reusable else None,
                completed_at=now if reusable else None,
            ))
        self._db.add_all(checkpoints)
        self._db.commit()
        self._db.refresh(job)
        return PreparedGeneration(job=job, should_process=True)

    async def process(self, project_id: uuid.UUID, job_id: uuid.UUID) -> GenerationJob:
        project = self._project(project_id)
        job = find_generation_job(self._db, self._workspace_id, project_id, job_id)
        if project is None:
            raise ProjectNotFoundError(f"Project {project_id} was not found")
        if job is None:
            raise LookupError(f"Generation job {job_id} was not found")
        if self._ai_service is None or self._matching is None:
            raise RuntimeError("Generation processing requires an initialized AI and matching service")

        cancelled = False
        try:
            if job.status in _TERMINAL_JOB_STATUSES:
                return job
            self._set_status(job, GenerationStatus.PARSING, progress=max(5, job.progress or 0))
            syllabus_text = project.syllabus.text if project.syllabus and project.syllabus.text else ""
            syllabus_items = self._syllabus_parser.flatten_hierarchy(syllabus_text)
            if not syllabus_items:
                return self._fail(job, project, "No usable syllabus items were found")

            items = self._job_items(job.id)
            if not items:
                self._db.add_all([
                    GenerationJobItem(
                        job_id=job.id,
                        position=index,
                        syllabus_chapter=entry.chapter,
                        syllabus_item=entry.title,
                        parent_syllabus_item=entry.parent_title,
                        status=GenerationItemStatus.PENDING.value,
                    )
                    for index, entry in enumerate(syllabus_items)
                ])
                job.total_items = len(syllabus_items)
                job.last_activity_at = datetime.now(UTC)
                self._db.commit()
                items = self._job_items(job.id)
            elif len(items) != len(syllabus_items):
                return self._fail(
                    job,
                    project,
                    "generation_checkpoint_mismatch: persisted syllabus checkpoints no longer match the current syllabus",
                )
            else:
                job.total_items = len(items)
                job.last_activity_at = datetime.now(UTC)
                self._db.commit()

            chunks = self._project_chunks(project.id)
            needs_matching = any(
                item.status not in _TERMINAL_ITEM_STATUSES and not item.candidates_payload
                for item in items
            )
            if needs_matching:
                self._set_status(job, GenerationStatus.MATCHING, progress=max(10, min(job.progress or 0, 35)))
                plans, matches = self._plan_and_match(syllabus_items, chunks, job=job)
                plans, matches = await self._apply_query_rewrite_fallbacks(plans, matches, chunks, job=job)
                matches = self._matching.resolve_dependent_matches(plans, matches)

                for index, match in enumerate(matches):
                    self._db.refresh(job)
                    if job.status in _TERMINAL_JOB_STATUSES:
                        return job
                    item = self._job_item(job.id, index)
                    if item is None:
                        raise RuntimeError(f"generation checkpoint {index} is missing")
                    if item.status in _TERMINAL_ITEM_STATUSES or item.candidates_payload:
                        continue
                    if not match.matched:
                        self._complete_item_failure(
                            item,
                            failure_type="unmatched",
                            reason=self._matching.failure_diagnostic(match),
                        )
                        self._record_progress(job)
                        continue
                    evidence = await asyncio.to_thread(
                        self._matching.select_generation_evidence,
                        match=match,
                        chunks=chunks,
                    )
                    item.candidates_payload = [candidate.model_dump(mode="json") for candidate in evidence]
                    item.error_message = None
                    item.failure_type = None
                    job.progress = max(15, min(35, 15 + int(20 * (index + 1) / max(job.total_items, 1))))
                    job.last_activity_at = datetime.now(UTC)
                    self._db.commit()

            for item in self._job_items(job.id):
                self._db.refresh(job)
                if job.status in _TERMINAL_JOB_STATUSES:
                    return job
                if item.status in _TERMINAL_ITEM_STATUSES:
                    continue
                if not item.candidates_payload:
                    self._complete_item_failure(
                        item,
                        failure_type="generation_error",
                        reason="generation checkpoint has no matched evidence",
                    )
                    self._record_progress(job)
                    continue
                evidence = [CandidateChunk.model_validate(candidate) for candidate in item.candidates_payload]
                item.status = GenerationItemStatus.PROCESSING.value
                item.started_at = item.started_at or datetime.now(UTC)
                self._set_status(job, GenerationStatus.GENERATING, commit=False)
                self._db.commit()
                try:
                    result = await self._ai_service.generate_item(
                        ItemGenerationRequest(
                            project_id=project.id,
                            project_name=project.name,
                            project_description=project.description,
                            syllabus_chapter=item.syllabus_chapter,
                            syllabus_item=item.syllabus_item,
                            candidates=evidence,
                        ),
                        before_validation=lambda: self._set_status(job, GenerationStatus.VALIDATING),
                    )
                    self._db.refresh(job)
                    if job.status in _TERMINAL_JOB_STATUSES:
                        return job
                    item = self._db.get(GenerationJobItem, item.id) or item
                    item.status = GenerationItemStatus.SUCCEEDED.value
                    item.result_payload = result.model_dump(mode="json")
                    item.completed_at = datetime.now(UTC)
                    item.error_message = "；".join(result.format_warnings) if result.format_warnings else None
                    item.failure_type = "format_warning" if result.format_warnings else None
                    self._db.commit()
                except Exception as exc:
                    self._db.rollback()
                    job = self._db.get(GenerationJob, job.id) or job
                    project = self._db.get(Project, project.id) or project
                    item = self._db.get(GenerationJobItem, item.id) or item
                    failure_type = "schema_validation" if isinstance(exc, AIOutputValidationError) else "generation_error"
                    self._complete_item_failure(item, failure_type=failure_type, reason=self._safe_error(exc))
                self._record_progress(job)

            return self._finalize_from_checkpoints(job, project)
        except asyncio.CancelledError:
            cancelled = True
            self._db.rollback()
            raise
        except Exception as exc:
            self._db.rollback()
            failed_job = self._db.get(GenerationJob, job.id) or job
            failed_project = self._db.get(Project, project.id) or project
            return self._fail(failed_job, failed_project, f"generation_failed: {self._safe_error(exc)}")
        finally:
            if not cancelled:
                self._ensure_terminal_after_worker(job_id, project_id)

    def _plan_and_match(
        self,
        syllabus_items: list,
        chunks: list[TextChunk],
        *,
        job: GenerationJob | None = None,
    ) -> tuple[list[QueryPlan], list[ItemMatch]]:
        if self._matching is None:
            raise RuntimeError("Matching service is not initialized")
        plans = self._matching.plan_items(syllabus_items)
        matches: list[ItemMatch] = []
        for index, plan in enumerate(plans):
            matches.append(self._matching.match_plan(plan=plan, chunks=chunks))
            if job is not None:
                job.progress = max(job.progress or 0, min(24, 10 + int(14 * (index + 1) / max(len(plans), 1))))
                job.last_activity_at = datetime.now(UTC)
                self._db.commit()
        return plans, matches

    async def _apply_query_rewrite_fallbacks(
        self,
        plans: list[QueryPlan],
        matches: list[ItemMatch],
        chunks: list[TextChunk],
        *,
        job: GenerationJob | None = None,
    ) -> tuple[list[QueryPlan], list[ItemMatch]]:
        if self._ai_service is None or self._matching is None:
            return plans, matches
        rewrite_cache: dict[str, list[str]] = {}
        for index, (plan, match) in enumerate(zip(plans, matches, strict=True)):
            if match.matched or plan.item_type != SyllabusItemType.KNOWLEDGE:
                continue
            cache_key = normalize_query_key(plan.original_query)
            if cache_key not in rewrite_cache:
                try:
                    rewrite_cache[cache_key] = await self._ai_service.rewrite_retrieval_queries(
                        syllabus_item=plan.original_query,
                        hierarchy_context=list(filter(None, (
                            plan.hierarchy_context.chapter,
                            plan.hierarchy_context.parent_title,
                            *plan.hierarchy_context.related_titles,
                        ))),
                    )
                except Exception:
                    rewrite_cache[cache_key] = []
            plans[index] = plan.with_ai_queries(rewrite_cache[cache_key])
            matches[index] = await asyncio.to_thread(
                self._matching.match_plan,
                plan=plans[index],
                chunks=chunks,
            )
            if job is not None:
                job.progress = max(job.progress or 0, min(30, 15 + int(15 * (index + 1) / max(len(plans), 1))))
                job.last_activity_at = datetime.now(UTC)
                self._db.commit()
        return plans, matches

    def get_job(self, project_id: uuid.UUID, job_id: uuid.UUID) -> GenerationJob | None:
        project = self._project(project_id, lock=True)
        if project is None:
            return None
        job = find_generation_job(self._db, self._workspace_id, project_id, job_id)
        if job is None:
            self._db.rollback()
            return None
        self._recover_job_locked(job, project)
        self._db.commit()
        self._db.refresh(job)
        return job

    def get_latest_job(self, project_id: uuid.UUID) -> GenerationJob | None:
        project = self._project(project_id, lock=True)
        if project is None:
            return None
        jobs = self._active_jobs(project_id)
        for duplicate in jobs[1:]:
            self._mark_failed(
                duplicate,
                project,
                "superseded_concurrent_generation: another active generation job owns this project",
            )
        if jobs:
            self._recover_job_locked(jobs[0], project)
        latest = self._db.scalar(
            select(GenerationJob)
            .where(GenerationJob.project_id == project_id)
            .order_by(GenerationJob.started_at.desc(), GenerationJob.created_at.desc())
            .limit(1)
        )
        self._db.commit()
        if latest is not None:
            self._db.refresh(latest)
        return latest

    def get_latest_published_job(self, project_id: uuid.UUID) -> GenerationJob | None:
        project = self._project(project_id)
        if project is None:
            return None
        return self._db.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.project_id == project_id,
                GenerationJob.status.in_((GenerationStatus.COMPLETED, GenerationStatus.PARTIAL_FAILED)),
            )
            .order_by(GenerationJob.completed_at.desc(), GenerationJob.created_at.desc())
            .limit(1)
        )

    def _project(self, project_id: uuid.UUID, *, lock: bool = False) -> Project | None:
        statement = select(Project).where(
            Project.id == project_id,
            Project.workspace_id == self._workspace_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self._db.scalar(statement)

    def _active_jobs(self, project_id: uuid.UUID) -> list[GenerationJob]:
        return list(self._db.scalars(
            select(GenerationJob)
            .where(
                GenerationJob.project_id == project_id,
                GenerationJob.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .order_by(GenerationJob.started_at.desc(), GenerationJob.created_at.desc())
            .with_for_update()
        ).all())

    def _retryable_partial_items(
        self,
        job: GenerationJob | None,
        syllabus_items: list,
    ) -> list[GenerationJobItem] | None:
        if job is None or job.status != GenerationStatus.PARTIAL_FAILED:
            return None
        items = self._job_items(job.id)
        if len(items) != len(syllabus_items):
            return None
        for index, (item, entry) in enumerate(zip(items, syllabus_items, strict=True)):
            if (
                item.position != index
                or item.syllabus_item != entry.title
                or item.syllabus_chapter != entry.chapter
                or item.parent_syllabus_item != entry.parent_title
            ):
                return None
        return items

    def _latest_reusable_job(self, project_id: uuid.UUID) -> GenerationJob | None:
        return self._db.scalar(
            select(GenerationJob)
            .join(Project, GenerationJob.project_id == Project.id)
            .where(
                GenerationJob.project_id == project_id,
                Project.workspace_id == self._workspace_id,
                GenerationJob.status.in_([GenerationStatus.COMPLETED, GenerationStatus.PARTIAL_FAILED]),
            )
            .order_by(GenerationJob.started_at.desc(), GenerationJob.created_at.desc())
            .limit(1)
        )

    def _project_chunks(self, project_id: uuid.UUID) -> list[TextChunk]:
        statement = (
            select(TextChunk)
            .join(ParsedDocument, TextChunk.parsed_document_id == ParsedDocument.id)
            .join(Document, ParsedDocument.document_id == Document.id)
            .where(
                Document.project_id == project_id,
                Document.kind == DocumentKind.COURSE_MATERIAL,
            )
            .order_by(TextChunk.page_start, TextChunk.position)
        )
        return list(self._db.scalars(statement).all())

    def _set_status(
        self,
        job: GenerationJob,
        status: GenerationStatus,
        *,
        progress: int | None = None,
        commit: bool = True,
    ) -> None:
        if status in _ACTIVE_JOB_STATUSES:
            self._db.refresh(job)
            if job.status in _TERMINAL_JOB_STATUSES:
                return
        job.status = status
        history = list(job.status_history or [])
        if not history or history[-1] != status.value:
            history.append(status.value)
        job.status_history = history
        if progress is not None:
            job.progress = progress
        job.last_activity_at = datetime.now(UTC)
        if commit:
            self._db.commit()

    def _record_progress(self, job: GenerationJob) -> None:
        self._db.flush()
        items = self._job_items(job.id)
        terminal = [item for item in items if item.status in _TERMINAL_ITEM_STATUSES]
        failures = self._item_failures(items)
        warnings = self._item_warnings(items)
        job.processed_items = len(terminal)
        job.successful_items = sum(item.status == GenerationItemStatus.SUCCEEDED.value for item in terminal)
        job.failed_items = len(failures)
        job.item_failures = [*failures, *warnings]
        job.progress = min(95, 20 + int(75 * len(terminal) / max(job.total_items, 1)))
        job.last_activity_at = datetime.now(UTC)
        self._db.commit()

    def _finalize_from_checkpoints(
        self,
        job: GenerationJob,
        project: Project,
        *,
        commit: bool = True,
    ) -> GenerationJob:
        locked_project = self._project(project.id, lock=True)
        if locked_project is None:
            raise ProjectNotFoundError(f"Project {project.id} was not found")
        project = locked_project
        self._db.refresh(job)
        if job.status in _TERMINAL_JOB_STATUSES:
            return job
        items = self._job_items(job.id)
        if len(items) != job.total_items or any(item.status not in _TERMINAL_ITEM_STATUSES for item in items):
            raise RuntimeError("generation_finalization_incomplete: not every syllabus item is terminal")
        failures = self._item_failures(items)
        warnings = self._item_warnings(items)
        succeeded = [item for item in items if item.status == GenerationItemStatus.SUCCEEDED.value]
        if not succeeded:
            self._mark_failed(job, project, "generation_failed: no syllabus item produced valid learning material", failures)
            if commit:
                self._db.commit()
                self._db.refresh(job)
            return job

        try:
            records = [self._record_from_item(item) for item in succeeded]
            organized = organize_generated_records(records)
            with self._db.begin_nested():
                self._replace_learning_material(project, organized)
                self._db.flush()
        except Exception as exc:
            self._mark_failed(
                job,
                project,
                f"finalization_failed: {self._safe_error(exc)}",
                failures,
            )
            if commit:
                self._db.commit()
                self._db.refresh(job)
            return job

        success_count = len(succeeded)
        failure_count = len(failures)
        project.status = ProjectStatus.COMPLETED
        job.processed_items = job.total_items
        job.successful_items = success_count
        job.failed_items = failure_count
        job.item_failures = [*failures, *warnings]
        job.completed_at = datetime.now(UTC)
        job.last_activity_at = job.completed_at
        if failures:
            summary = self._failure_summary(items)
            job.error_message = (
                f"generation_partial_failed: {success_count} succeeded, {failure_count} failed ({summary})"
            )
            self._set_status(job, GenerationStatus.PARTIAL_FAILED, progress=100, commit=False)
        else:
            job.error_message = None
            self._set_status(job, GenerationStatus.COMPLETED, progress=100, commit=False)
        if commit:
            self._db.commit()
            self._db.refresh(job)
        return job

    def _fail(
        self,
        job: GenerationJob,
        project: Project,
        message: str,
        failures: list[dict[str, object]] | None = None,
    ) -> GenerationJob:
        self._mark_failed(job, project, message, failures)
        self._db.commit()
        self._db.refresh(job)
        return job

    def _mark_failed(
        self,
        job: GenerationJob,
        project: Project,
        message: str,
        failures: list[dict[str, object]] | None = None,
    ) -> None:
        project.status = ProjectStatus.COMPLETED if project.chapters else ProjectStatus.FAILED
        job.item_failures = list(failures or job.item_failures or [])
        job.successful_items = max(0, int(job.processed_items or 0) - len(job.item_failures))
        job.failed_items = len(job.item_failures)
        job.error_message = message
        job.completed_at = datetime.now(UTC)
        job.last_activity_at = job.completed_at
        job.progress = 100
        self._set_status(job, GenerationStatus.FAILED, progress=100, commit=False)

    def _recover_job_locked(self, job: GenerationJob, project: Project) -> None:
        if job.status in _TERMINAL_JOB_STATUSES:
            return
        items = self._job_items(job.id)
        if items and len(items) == job.total_items and all(
            item.status in _TERMINAL_ITEM_STATUSES for item in items
        ):
            self._finalize_from_checkpoints(job, project, commit=False)
            return
        if self._is_stale(job, items):
            self._mark_failed(
                job,
                project,
                "stale_interrupted_generation: no generation activity was recorded before the recovery deadline",
                self._item_failures(items),
            )

    def _is_stale(self, job: GenerationJob, items: list[GenerationJobItem]) -> bool:
        activity = [job.last_activity_at]
        activity.extend(item.updated_at or item.completed_at or item.started_at for item in items)
        latest = max(
            (self._utc(value) for value in activity if value is not None),
            default=self._utc(job.started_at or job.created_at),
        )
        return datetime.now(UTC) - latest >= self._stale_after

    def _ensure_terminal_after_worker(self, job_id: uuid.UUID, project_id: uuid.UUID) -> None:
        try:
            self._db.rollback()
            job = find_generation_job(self._db, self._workspace_id, project_id, job_id)
            if job is None or job.status in _TERMINAL_JOB_STATUSES:
                return
            project = self._project(project_id)
            if project is not None:
                self._fail(
                    job,
                    project,
                    "generation_finalization_incomplete: generation worker exited without a terminal state",
                )
        except Exception:
            self._db.rollback()

    def _job_items(self, job_id: uuid.UUID) -> list[GenerationJobItem]:
        return list(self._db.scalars(
            select(GenerationJobItem)
            .where(GenerationJobItem.job_id == job_id)
            .order_by(GenerationJobItem.position)
        ).all())

    def _job_item(self, job_id: uuid.UUID, position: int) -> GenerationJobItem | None:
        return self._db.scalar(select(GenerationJobItem).where(
            GenerationJobItem.job_id == job_id,
            GenerationJobItem.position == position,
        ))

    @staticmethod
    def _complete_item_failure(item: GenerationJobItem, *, failure_type: str, reason: str) -> None:
        item.status = GenerationItemStatus.FAILED.value
        item.failure_type = failure_type
        item.error_message = reason
        item.completed_at = datetime.now(UTC)

    @staticmethod
    def _item_failures(items: list[GenerationJobItem]) -> list[dict[str, object]]:
        return [
            {
                "syllabus_item": item.syllabus_item,
                "reason": item.error_message or item.failure_type or "failed",
                "failure_type": item.failure_type,
                "position": item.position,
                "syllabus_chapter": item.syllabus_chapter,
                "parent_syllabus_item": item.parent_syllabus_item,
            }
            for item in items
            if item.status == GenerationItemStatus.FAILED.value
        ]

    @staticmethod
    def _item_warnings(items: list[GenerationJobItem]) -> list[dict[str, object]]:
        return [
            {
                "syllabus_item": item.syllabus_item,
                "reason": item.error_message or "部分格式异常，已保留可读内容",
                "failure_type": "format_warning",
                "position": item.position,
                "syllabus_chapter": item.syllabus_chapter,
                "parent_syllabus_item": item.parent_syllabus_item,
            }
            for item in items
            if (
                item.status == GenerationItemStatus.SUCCEEDED.value
                and item.failure_type == "format_warning"
            )
        ]

    @staticmethod
    def _failure_summary(items: list[GenerationJobItem]) -> str:
        counts: dict[str, int] = {}
        for item in items:
            if item.status != GenerationItemStatus.FAILED.value:
                continue
            key = item.failure_type or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"

    @staticmethod
    def _record_from_item(item: GenerationJobItem) -> GeneratedRecord:
        if item.result_payload is None or item.candidates_payload is None:
            raise RuntimeError(f"successful generation checkpoint {item.position} has no durable result")
        return GeneratedRecord(
            syllabus_chapter=item.syllabus_chapter,
            syllabus_item=item.syllabus_item,
            parent_syllabus_item=item.parent_syllabus_item,
            result=GeneratedItemResult.model_validate(item.result_payload),
            candidates=[CandidateChunk.model_validate(candidate) for candidate in item.candidates_payload],
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _replace_learning_material(self, project: Project, records: list[GeneratedRecord]) -> None:
        project.chapters.clear()
        self._db.flush()
        reliable_chapter_by_chunk_id: dict[uuid.UUID, str] = {}
        for document in project.documents:
            if document.kind != DocumentKind.COURSE_MATERIAL or document.parsed_document is None:
                continue
            reliable_chapter_by_chunk_id.update(
                build_reliable_source_chapter_index(document.parsed_document.chunks)
            )
        course_document = next(
            (document for document in reversed(project.documents) if document.kind == DocumentKind.COURSE_MATERIAL),
            None,
        )
        source_title = Path(course_document.original_name).stem.strip() if course_document is not None else "资料"
        grouped: OrderedDict[str, list[GeneratedRecord]] = OrderedDict()
        for record in records:
            grouped.setdefault(
                self._source_chapter(
                    record,
                    source_title or "资料",
                    reliable_chapter_by_chunk_id=reliable_chapter_by_chunk_id,
                ),
                [],
            ).append(record)

        for chapter_position, (chapter_title, chapter_records) in enumerate(grouped.items()):
            chapter = Chapter(title=chapter_title, position=chapter_position)
            for point_position, record in enumerate(chapter_records):
                knowledge_point = KnowledgePoint(
                    title=record.result.knowledge_point_title,
                    position=point_position,
                )
                candidate_map = {candidate.chunk_id: candidate for candidate in record.candidates}
                for bullet_position, generated in enumerate(record.result.bullet_points):
                    bullet = BulletPoint(id=uuid.uuid4(), position=bullet_position)
                    bullet.versions = [
                        ContentVersion(
                            kind=ContentVersionKind.ORIGINAL,
                            title=generated.title,
                            content=generated.original.content,
                        ),
                        ContentVersion(
                            kind=ContentVersionKind.RECITATION,
                            title=generated.title,
                            content=generated.recitation.content,
                        ),
                        ContentVersion(
                            kind=ContentVersionKind.KEYWORDS,
                            title=generated.title,
                            content=generated.keywords.content,
                        ),
                    ]
                    bullet.sources = [
                        self._source_link(candidate_map[source_id], generated.source_pages)
                        for source_id in generated.source_chunk_ids
                    ]
                    knowledge_point.bullet_points.append(bullet)
                chapter.knowledge_points.append(knowledge_point)
            project.chapters.append(chapter)

    @staticmethod
    def _source_chapter(
        record: GeneratedRecord,
        source_title: str = "资料",
        reliable_chapter_by_chunk_id: dict[uuid.UUID, str] | None = None,
    ) -> str:
        cited_ids = [
            source_id
            for bullet in record.result.bullet_points
            for source_id in bullet.source_chunk_ids
        ]
        return (
            resolve_source_chapter_title(
                record.candidates,
                cited_ids,
                reliable_chapter_by_chunk_id=reliable_chapter_by_chunk_id,
            )
            or INTERNAL_UNRESOLVED_CHAPTER
        )

    @staticmethod
    def _source_link(candidate: CandidateChunk, cited_pages: list[int]) -> BulletPointSource:
        relevant_pages = [
            page for page in cited_pages if candidate.page_start <= page <= candidate.page_end
        ]
        return BulletPointSource(
            text_chunk_id=candidate.chunk_id,
            page_start=min(relevant_pages) if relevant_pages else candidate.page_start,
            page_end=max(relevant_pages) if relevant_pages else candidate.page_end,
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        return message[:1000]
