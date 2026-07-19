import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session, sessionmaker

from app.ai.clients.base import AIConfigurationError
from app.ai.clients.factory import build_ai_client
from app.ai.service import AIService
from app.auth.dependencies import WritableWorkspaceId, WorkspaceId
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.matching.service import MatchingService
from app.schemas.project import GenerationJobRead
from app.services.generation import GenerationWorkflowService, ProjectNotFoundError
from app.settings.security import CredentialCipher, get_transport_key_pair
from app.settings.service import DeepSeekSettingsService

router = APIRouter()


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
    def __init__(self, session_factory: Callable[[], Session], settings: Settings) -> None:
        self._session_factory = session_factory
        self._settings = settings

    async def run(self, workspace_id: uuid.UUID, project_id: uuid.UUID, job_id: uuid.UUID) -> None:
        with self._session_factory() as db:
            service = build_generation_service(db, self._settings, workspace_id)
            await service.process(project_id, job_id)


def get_generation_task_runner(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GenerationTaskRunner:
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    return GenerationTaskRunner(factory, settings)


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
    background_tasks: BackgroundTasks,
    regenerate: bool = False,
) -> GenerationJobRead:
    try:
        service = build_generation_service(db, settings, workspace_id)
        prepared = service.prepare(project_id, regenerate=regenerate)
        response = GenerationJobRead.model_validate(prepared.job)
        if prepared.should_process:
            background_tasks.add_task(runner.run, workspace_id, project_id, prepared.job.id)
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
