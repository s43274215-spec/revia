from __future__ import annotations

import asyncio
import hmac
import logging
import os
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel


_LOGGER = logging.getLogger("revia.ocr.service")
_MAX_IMAGE_BYTES = int(os.getenv("OCR_MAX_IMAGE_MB", "20")) * 1024 * 1024
_THREADS = max(1, int(os.getenv("OCR_THREADS", "1")))
_API_KEY = os.getenv("OCR_API_KEY", "").strip()
_engine = None
_engine_lock = asyncio.Lock()
_inference_gate = asyncio.Semaphore(1)


class OCRResponse(BaseModel):
    text: str
    character_count: int
    engine_version: str


app = FastAPI(title="Revia OCR Service", version="1.0.0")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "engine_initialized": _engine is not None,
        "api_key_configured": bool(_API_KEY),
    }


@app.post("/v1/ocr", response_model=OCRResponse)
async def recognize(
    request: Request,
    authorization: str | None = Header(default=None),
) -> OCRResponse:
    _verify_authorization(authorization)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].casefold()
    if content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(status_code=415, detail="只支持 PNG、JPEG 或 WebP 图像")
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="OCR 图像过大")
    image = await request.body()
    if not image:
        raise HTTPException(status_code=422, detail="OCR 图像为空")
    if len(image) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="OCR 图像过大")

    async with _inference_gate:
        engine = await _get_engine()
        try:
            text = await asyncio.to_thread(_recognize_sync, engine, image)
        except Exception as exc:
            _LOGGER.exception("ocr_inference_failed error_type=%s", exc.__class__.__name__)
            raise HTTPException(status_code=503, detail="OCR 推理暂时失败") from exc
    return OCRResponse(
        text=text,
        character_count=len(text),
        engine_version=_rapidocr_version(),
    )


def _verify_authorization(authorization: str | None) -> None:
    if not _API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OCR_API_KEY 未配置",
        )
    prefix = "Bearer "
    supplied = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
    if not supplied or not hmac.compare_digest(supplied, _API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OCR 服务鉴权失败",
        )


async def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    async with _engine_lock:
        if _engine is None:
            _engine = await asyncio.to_thread(_build_engine)
    return _engine


def _build_engine():
    _configure_threads()
    from rapidocr import RapidOCR

    return RapidOCR(params={
        "Global.log_level": "error",
        "Global.max_side_len": 2048,
        "Det.limit_type": "max",
        "Det.limit_side_len": 2048,
        "Cls.cls_batch_num": 1,
        "Rec.rec_batch_num": 1,
        "EngineConfig.onnxruntime.intra_op_num_threads": _THREADS,
        "EngineConfig.onnxruntime.inter_op_num_threads": _THREADS,
    })


def _recognize_sync(engine, image: bytes) -> str:
    result = engine(image)
    texts = getattr(result, "txts", None) or ()
    return "\n".join(str(value).strip() for value in texts if str(value).strip())


def _configure_threads() -> None:
    value = str(_THREADS)
    for name in (
        "OMP_NUM_THREADS",
        "OMP_THREAD_LIMIT",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "ORT_NUM_THREADS",
    ):
        os.environ[name] = value


def _rapidocr_version() -> str:
    try:
        return version("rapidocr")
    except PackageNotFoundError:
        return "unknown"
