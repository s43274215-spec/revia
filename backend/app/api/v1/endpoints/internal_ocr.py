from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.endpoints.documents import build_document_processing_service
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.document.parser import SourceOutlineEntry
from app.schemas.github_ocr import (
    GitHubOCRClaimRead,
    GitHubOCRClaimRequest,
    GitHubOCRFailureRequest,
    GitHubOCRFinishRequest,
    GitHubOCRHeartbeatRequest,
    GitHubOCRPageResult,
    GitHubOCRProgressRead,
)
from app.services.github_ocr import (
    GitHubOCRJobConflictError,
    GitHubOCRJobNotFoundError,
    GitHubOCRJobService,
)
from app.services.storage import StorageError, build_storage_provider


router = APIRouter(include_in_schema=False)


def require_github_ocr_worker(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.github_ocr_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub OCR 尚未配置",
        )
    prefix = "Bearer "
    supplied = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
    if not supplied or not hmac.compare_digest(supplied, settings.github_ocr_worker_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="GitHub OCR Worker 鉴权失败",
        )


def get_github_ocr_job_service(
    _: Annotated[None, Depends(require_github_ocr_worker)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GitHubOCRJobService:
    storage = build_storage_provider(settings)
    return GitHubOCRJobService(
        db=db,
        storage=storage,
        processing=build_document_processing_service(db, settings),
        lease_seconds=settings.github_ocr_lease_seconds,
        download_url_expires_seconds=settings.github_ocr_download_url_expires_seconds,
        ocr_dpi=settings.ocr_dpi,
        minimum_text_length=settings.ocr_minimum_text_length,
        max_pdf_pages=settings.max_pdf_pages,
    )


JobService = Annotated[GitHubOCRJobService, Depends(get_github_ocr_job_service)]


@router.post("/jobs/{document_id}/claim", response_model=GitHubOCRClaimRead)
def claim_job(
    document_id: uuid.UUID,
    payload: GitHubOCRClaimRequest,
    service: JobService,
) -> GitHubOCRClaimRead:
    try:
        return service.claim(document_id, payload.attempt_id)
    except GitHubOCRJobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GitHubOCRJobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post("/jobs/{document_id}/heartbeat", response_model=GitHubOCRProgressRead)
def heartbeat_job(
    document_id: uuid.UUID,
    payload: GitHubOCRHeartbeatRequest,
    service: JobService,
) -> GitHubOCRProgressRead:
    try:
        return service.heartbeat(
            document_id,
            payload.attempt_id,
            current_page=payload.current_page,
        )
    except GitHubOCRJobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GitHubOCRJobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/jobs/{document_id}/pages/{page_number}", response_model=GitHubOCRProgressRead)
def complete_page(
    document_id: uuid.UUID,
    page_number: int,
    payload: GitHubOCRPageResult,
    service: JobService,
) -> GitHubOCRProgressRead:
    try:
        return service.complete_page(
            document_id,
            payload.attempt_id,
            page_number,
            text=payload.text,
            extraction_method=payload.extraction_method,
        )
    except GitHubOCRJobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GitHubOCRJobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/jobs/{document_id}/finish", response_model=GitHubOCRProgressRead)
def finish_job(
    document_id: uuid.UUID,
    payload: GitHubOCRFinishRequest,
    service: JobService,
) -> GitHubOCRProgressRead:
    try:
        document = service.finish(
            document_id,
            payload.attempt_id,
            outline=tuple(
                SourceOutlineEntry(
                    level=entry.level,
                    title=entry.title,
                    page_number=entry.page_number,
                )
                for entry in payload.outline
            ),
        )
    except GitHubOCRJobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GitHubOCRJobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return GitHubOCRProgressRead(
        document_id=document.id,
        processing_status=document.processing_status.value,
        processing_phase=document.processing_phase,
        processed_pages=document.processed_pages,
        total_pages=document.total_pages,
        current_page=document.current_page,
    )


@router.post("/jobs/{document_id}/fail", response_model=GitHubOCRProgressRead)
def fail_job(
    document_id: uuid.UUID,
    payload: GitHubOCRFailureRequest,
    service: JobService,
) -> GitHubOCRProgressRead:
    try:
        document = service.fail(
            document_id,
            payload.attempt_id,
            page_number=payload.page_number,
            error_message=payload.error_message,
        )
    except GitHubOCRJobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GitHubOCRJobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return GitHubOCRProgressRead(
        document_id=document.id,
        processing_status=document.processing_status.value,
        processing_phase=document.processing_phase,
        processed_pages=document.processed_pages,
        total_pages=document.total_pages,
        current_page=document.current_page,
    )
