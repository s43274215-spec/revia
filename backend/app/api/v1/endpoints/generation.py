import asyncio
import logging
import threading
import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai.clients.base import AIConfigurationError
from app.ai.clients.factory import build_ai_client
from app.ai.service import AIService
from app.auth.dependencies import WritableWorkspaceId, WorkspaceId
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.matching.service import MatchingService
from app.models.enums import GenerationStatus
from app.models.project import GenerationJob, Project
from app.schemas.project import GenerationJobRead
from app.services.generation import GenerationWorkflowService, ProjectNotFoundError
from app.settings.security import CredentialCipher, get_transport_key_pair
from app.settings.service import DeepSeekSettingsService

router = APIRouter()
logger = logging.getLogger("revia.generation")

_ACTIVE_GENERATION_STATUSES = (
    GenerationStatus.PENDING,
    GenerationStatus.PARSING,
    GenerationStatus.MATCHING,
    GenerationStatus.GENERATING,
    GenerationStatus.VALIDATING,
)


def build_generation_service(
    db: Session,
    settings: Settings,
    workspace_id: uuid.UUID,
    *,
    initialize_ai: bool = True,
) -> GenerationWorkflowService:
    api_key = None
    if initialize_ai and settings.ai_mode == "live":
        api_key = DeepSeekSettingsService(
            db=db,
            workspace_id=workspace_id,
            settings=settings,
            cipher=CredentialCipher(settings.credential_encryption_key),
            transport=get_transport_key_pair(),
        ).read_api_key()
    ai_service = None
    matching_service = None
    if initialize_ai:
        try:
            client = build_ai_client(settings, api_key)
        except AIConfigurationError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        ai_service = AIService(client)
        matching_service = MatchingService(
            threshold=settings.matching_threshold,
            max_candidates=settings.matching_max_candidates,
        )
    provider_name = "mock" if settings.ai_mode == "mock" else settings.ai_provider
    return GenerationWorkflowService(
        db=db,
        workspace_id=workspace_id,
        ai_service=ai_service,
        matching_service=matching_service,
        provider_name=provider_name,
        stale_after_seconds=settings.generation_stale_seconds,
    )


class GenerationTaskRunner:
    _local_guard = threading.Lock()
    _local_active_jobs: set[uuid.UUID] = set()

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        bind: Engine,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._bind = bind

    def dispatch(self, workspace_id: uuid.UUID, project_id: uuid.UUID, job_id: uuid.UUID) -> None:
        task = asyncio.create_task(self.run(workspace_id, project_id, job_id))
        task.add_done_callback(self._log_dispatch_result)

    async def run(self, workspace_id: uuid.UUID, project_id: uuid.UUID, job_id: uuid.UUID) -> None:
        if not self._claim_local(job_id):
            return
        try:
            if self._bind.dialect.name == "postgresql":
                await self._run_with_postgres_lock(workspace_id, project_id, job_id)
            else:
                await self._run_service(self._session_factory, workspace_id, project_id, job_id)
        finally:
            self._release_local(job_id)

    async def resume_incomplete(self) -> bool:
        with self._session_factory() as db:
            row = db.execute(
                select(Project.workspace_id, GenerationJob.project_id, GenerationJob.id)
                .join(Project, GenerationJob.project_id == Project.id)
                .where(GenerationJob.status.in_(_ACTIVE_GENERATION_STATUSES))
                .order_by(GenerationJob.started_at.asc(), GenerationJob.created_at.asc())
                .limit(1)
            ).first()
        if row is None:
            return False
        workspace_id, project_id, job_id = row
        await self.run(workspace_id, project_id, job_id)
        return True

    async def _run_with_postgres_lock(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> None:
        lock_key = self._advisory_lock_key(job_id)
        connection = self._bind.connect()
        try:
            acquired = bool(connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}))
            if not acquired:
                return
            locked_factory = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False)
            try:
                await self._run_service(locked_factory, workspace_id, project_id, job_id)
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
                connection.commit()
        finally:
            connection.close()

    async def _run_service(
        self,
        factory: Callable[[], Session],
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> None:
        try:
            with factory() as db:
                service = build_generation_service(db, self._settings, workspace_id)
                await service.process(project_id, job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Persistent generation worker failed job_id=%s project_id=%s", job_id, project_id)

    @staticmethod
    def _log_dispatch_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.exception("Detached generation dispatch failed")

    @classmethod
    def _claim_local(cls, job_id: uuid.UUID) -> bool:
        with cls._local_guard:
            if job_id in cls._local_active_jobs:
                return False
            cls._local_active_jobs.add(job_id)
            return True

    @classmethod
    def _release_local(cls, job_id: uuid.UUID) -> None:
        with cls._local_guard:
            cls._local_active_jobs.discard(job_id)

    @staticmethod
    def _advisory_lock_key(job_id: uuid.UUID) -> int:
        value = job_id.int & ((1 << 63) - 1)
        return value or 1


def get_generation_task_runner(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GenerationTaskRunner:
    bind = db.get_bind()
    factory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    return GenerationTaskRunner(factory, settings, bind)


GenerationRunner = Annotated[GenerationTaskRunner, Depends(get_generation_task_runner)]
DbSession = Annotated[Session, Depends(get_db)]
RuntimeSettings = Annotated[Settings, Depends(get_settings)]


@router.post("/{project_id}/generation-jobs", response_model=GenerationJobRead, status_code=status.HTTP_202_ACCEPTED)
async def start_generation(
    project_id: uuid.UUID,
    workspace_id: WritableWorkspaceId,
    db: DbSession,
    settings: RuntimeSettings,
    runner: GenerationRunner,
    regenerate: bool = False,
) -> GenerationJobRead:
    try:
        service = build_generation_service(db, settings, workspace_id)
        prepared = service.prepare(project_id, regenerate=regenerate)
        response = GenerationJobRead.model_validate(prepared.job)
        if prepared.should_process:
            if settings.ai_mode == "mock":
                await runner.run(workspace_id, project_id, prepared.job.id)
            else:
                runner.dispatch(workspace_id, project_id, prepared.job.id)
        return response
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{project_id}/generation-jobs/latest", response_model=GenerationJobRead | None)
def get_latest_generation_job(
    project_id: uuid.UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    settings: RuntimeSettings,
) -> GenerationJobRead | None:
    service = build_generation_service(db, settings, workspace_id, initialize_ai=False)
    job = service.get_latest_job(project_id)
    return GenerationJobRead.model_validate(job) if job is not None else None


@router.get("/{project_id}/generation-jobs/latest-published", response_model=GenerationJobRead | None)
def get_latest_published_generation_job(
    project_id: uuid.UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    settings: RuntimeSettings,
) -> GenerationJobRead | None:
    service = build_generation_service(db, settings, workspace_id, initialize_ai=False)
    job = service.get_latest_published_job(project_id)
    return GenerationJobRead.model_validate(job) if job is not None else None


@router.get("/{project_id}/generation-jobs/{job_id}", response_model=GenerationJobRead)
def get_generation_job(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    settings: RuntimeSettings,
) -> GenerationJobRead:
    service = build_generation_service(db, settings, workspace_id, initialize_ai=False)
    job = service.get_job(project_id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generation job was not found")
    return GenerationJobRead.model_validate(job)
