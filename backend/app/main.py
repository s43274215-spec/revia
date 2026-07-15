import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.api.v1.endpoints.documents import build_document_processing_service
from app.core.config import get_settings
from app.db.session import SessionLocal
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


@asynccontextmanager
async def lifespan(application: FastAPI):
    runner = DocumentTaskRunner(
        SessionLocal,
        lambda db: build_document_processing_service(db, settings),
    )
    application.state.document_task_runner = runner
    async def keep_queue_moving() -> None:
        while True:
            try:
                await asyncio.to_thread(runner.resume_incomplete)
            except Exception:
                logging.getLogger("revia.documents").exception("Persistent document queue dispatch failed")
            await asyncio.sleep(min(30, max(5, settings.document_lease_seconds // 3)))

    resume_task = asyncio.create_task(keep_queue_moving())
    yield
    resume_task.cancel()
    try:
        await resume_task
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
