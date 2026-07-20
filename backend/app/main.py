import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.api.v1.endpoints.documents import build_document_processing_service
from app.api.v1.endpoints.generation import GenerationTaskRunner
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.services.document_processing import DocumentTaskRunner

settings = get_settings()


def configure_ai_audit_log() -> None:
    audit_logger = logging.getLogger("revia.ai")
    audit_logger.setLevel(logging.INFO)
    if audit_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    audit_logger.addHandler(handler)
    audit_logger.propagate = False


configure_ai_audit_log()


logging.getLogger("revia.documents").info(
    "document_runtime ocr_enabled=%s ocr_dpi=%d ocr_worker_max_rss_mb=%d "
    "ocr_worker_max_pages=%d ocr_container_memory_budget_mb=%d "
    "ocr_worker_threads=%d storage_backend=%s",
    settings.ocr_enabled,
    settings.ocr_dpi,
    settings.ocr_worker_max_rss_mb,
    settings.ocr_worker_max_pages,
    settings.ocr_container_memory_budget_mb,
    settings.ocr_worker_threads,
    settings.storage_backend,
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    runner = DocumentTaskRunner(
        SessionLocal,
        lambda db: build_document_processing_service(db, settings),
    )
    application.state.document_task_runner = runner
    generation_runner = GenerationTaskRunner(SessionLocal, settings, engine)
    application.state.generation_task_runner = generation_runner

    async def keep_document_queue_moving() -> None:
        while True:
            try:
                await asyncio.to_thread(runner.resume_incomplete)
            except Exception:
                logging.getLogger("revia.documents").exception("Persistent document queue dispatch failed")
            await asyncio.sleep(min(30, max(5, settings.document_lease_seconds // 3)))

    async def keep_generation_queue_moving() -> None:
        while True:
            try:
                worked = await generation_runner.resume_incomplete()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger("revia.generation").exception("Persistent generation queue dispatch failed")
                worked = False
            await asyncio.sleep(1 if worked else 5)

    resume_tasks = [
        asyncio.create_task(keep_document_queue_moving()),
        asyncio.create_task(keep_generation_queue_moving()),
    ]
    yield
    for task in resume_tasks:
        task.cancel()
    for task in resume_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
