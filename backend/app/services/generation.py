import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.service import AIService, ItemGenerationRequest
from app.matching.schemas import CandidateChunk
from app.matching.service import MatchingService
from app.models.content import BulletPoint, BulletPointSource, Chapter, ContentVersion, KnowledgePoint
from app.models.document import ParsedDocument, TextChunk
from app.models.enums import ContentVersionKind, DocumentKind, GenerationStatus, ProjectStatus
from app.models.project import Document, GenerationJob, Project
from app.services.knowledge_hierarchy import GeneratedRecord, organize_generated_records
from app.syllabus.parser import SyllabusParser


class ProjectNotFoundError(LookupError):
    pass


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
        ai_service: AIService,
        matching_service: MatchingService,
        provider_name: str,
        syllabus_parser: SyllabusParser | None = None,
    ) -> None:
        self._db = db
        self._workspace_id = workspace_id
        self._ai_service = ai_service
        self._matching = matching_service
        self._provider_name = provider_name
        self._syllabus_parser = syllabus_parser or SyllabusParser()

    async def start(self, project_id: uuid.UUID, *, regenerate: bool = False) -> GenerationJob:
        prepared = self.prepare(project_id, regenerate=regenerate)
        if not prepared.should_process:
            return prepared.job
        return await self.process(project_id, prepared.job.id)

    def prepare(self, project_id: uuid.UUID, *, regenerate: bool = False) -> PreparedGeneration:
        project = self._project(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Project {project_id} was not found")
        existing = self._latest_reusable_job(project_id)
        if existing is not None and not regenerate:
            return PreparedGeneration(job=existing, should_process=False)

        job = GenerationJob(
            project_id=project.id,
            status=GenerationStatus.PENDING,
            provider=self._provider_name,
            progress=0,
            processed_items=0,
            total_items=0,
            item_failures=[],
            status_history=[GenerationStatus.PENDING.value],
            started_at=datetime.now(UTC),
        )
        project.status = ProjectStatus.PROCESSING
        self._db.add(job)
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

        try:
            self._set_status(job, GenerationStatus.PARSING, progress=5)
            syllabus_text = project.syllabus.text if project.syllabus and project.syllabus.text else ""
            syllabus_items = self._syllabus_parser.flatten_hierarchy(syllabus_text)
            job.total_items = len(syllabus_items)
            self._db.commit()
            if not syllabus_items:
                return self._fail(job, project, "No usable syllabus items were found")

            chunks = self._project_chunks(project.id)
            self._set_status(job, GenerationStatus.MATCHING, progress=15)
            matches = [
                self._matching.match_item(
                    syllabus_item=entry.title,
                    syllabus_chapter=entry.chapter,
                    chunks=chunks,
                )
                for entry in syllabus_items
            ]
            self._db.commit()

            records: list[GeneratedRecord] = []
            failures: list[dict[str, str]] = []
            for index, match in enumerate(matches, start=1):
                if not match.matched:
                    failures.append({"syllabus_item": match.syllabus_item, "reason": match.reason or "unmatched"})
                    self._record_progress(job, index, failures)
                    continue
                self._set_status(job, GenerationStatus.GENERATING)
                evidence = self._matching.select_generation_evidence(match=match, chunks=chunks)
                try:
                    result = await self._ai_service.generate_item(
                        ItemGenerationRequest(
                            project_id=project.id,
                            project_name=project.name,
                            project_description=project.description,
                            syllabus_chapter=match.chapter,
                            syllabus_item=match.syllabus_item,
                            candidates=evidence,
                        ),
                        before_validation=lambda: self._set_status(job, GenerationStatus.VALIDATING),
                    )
                    records.append(GeneratedRecord(
                        syllabus_chapter=match.chapter,
                        syllabus_item=match.syllabus_item,
                        parent_syllabus_item=syllabus_items[index - 1].parent_title,
                        result=result,
                        candidates=evidence,
                    ))
                except Exception as exc:
                    failures.append({"syllabus_item": match.syllabus_item, "reason": self._safe_error(exc)})
                    self._db.rollback()
                    job = self._db.get(GenerationJob, job.id) or job
                    project = self._db.get(Project, project.id) or project
                self._record_progress(job, index, failures)

            if not records:
                return self._fail(job, project, "No syllabus item produced valid learning material", failures)

            self._replace_learning_material(project, organize_generated_records(records))
            project.status = ProjectStatus.COMPLETED
            final_status = GenerationStatus.PARTIAL_FAILED if failures else GenerationStatus.COMPLETED
            self._set_status(job, final_status, progress=100, commit=False)
            job.processed_items = job.total_items
            job.item_failures = failures
            job.error_message = f"{len(failures)} syllabus item(s) failed" if failures else None
            job.completed_at = datetime.now(UTC)
            self._db.commit()
            self._db.refresh(job)
            return job
        except Exception as exc:
            self._db.rollback()
            failed_job = self._db.get(GenerationJob, job.id) or job
            failed_project = self._db.get(Project, project.id) or project
            return self._fail(failed_job, failed_project, self._safe_error(exc))

    def get_job(self, project_id: uuid.UUID, job_id: uuid.UUID) -> GenerationJob | None:
        return find_generation_job(self._db, self._workspace_id, project_id, job_id)

    def _project(self, project_id: uuid.UUID) -> Project | None:
        return self._db.scalar(select(Project).where(
            Project.id == project_id,
            Project.workspace_id == self._workspace_id,
        ))

    def _latest_reusable_job(self, project_id: uuid.UUID) -> GenerationJob | None:
        return self._db.scalar(
            select(GenerationJob)
            .join(Project, GenerationJob.project_id == Project.id)
            .where(
                GenerationJob.project_id == project_id,
                Project.workspace_id == self._workspace_id,
                GenerationJob.status.in_([
                    GenerationStatus.PENDING,
                    GenerationStatus.PARSING,
                    GenerationStatus.MATCHING,
                    GenerationStatus.GENERATING,
                    GenerationStatus.VALIDATING,
                    GenerationStatus.COMPLETED,
                    GenerationStatus.PARTIAL_FAILED,
                ]),
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
        job.status = status
        history = list(job.status_history or [])
        if not history or history[-1] != status.value:
            history.append(status.value)
        job.status_history = history
        if progress is not None:
            job.progress = progress
        if commit:
            self._db.commit()

    def _record_progress(
        self,
        job: GenerationJob,
        processed: int,
        failures: list[dict[str, str]],
    ) -> None:
        job.processed_items = processed
        job.item_failures = list(failures)
        job.progress = min(95, 20 + int(75 * processed / max(job.total_items, 1)))
        self._db.commit()

    def _fail(
        self,
        job: GenerationJob,
        project: Project,
        message: str,
        failures: list[dict[str, str]] | None = None,
    ) -> GenerationJob:
        project.status = ProjectStatus.COMPLETED if project.chapters else ProjectStatus.FAILED
        job.item_failures = list(failures or job.item_failures or [])
        job.error_message = message
        job.completed_at = datetime.now(UTC)
        job.progress = 100
        self._set_status(job, GenerationStatus.FAILED, progress=100, commit=False)
        self._db.commit()
        self._db.refresh(job)
        return job

    def _replace_learning_material(self, project: Project, records: list[GeneratedRecord]) -> None:
        project.chapters.clear()
        self._db.flush()
        grouped: OrderedDict[str | None, list[GeneratedRecord]] = OrderedDict()
        for record in records:
            grouped.setdefault(record.syllabus_chapter, []).append(record)

        for chapter_position, (chapter_title, chapter_records) in enumerate(grouped.items()):
            chapter = Chapter(title=chapter_title or "未分章", position=chapter_position)
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
