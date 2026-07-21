from __future__ import annotations

import gc
import logging
import os
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path

from app.core.memory import (
    container_memory_mb,
    log_ocr_memory,
    process_rss_mb,
    release_process_memory,
)


_WORKER_LOGGER = logging.getLogger("revia.ocr.worker")


class OCRUnavailableError(RuntimeError):
    pass


class OCRWorkerError(RuntimeError):
    pass


class OCRWorkerResourceError(OCRWorkerError):
    pass


@dataclass(frozen=True)
class OCRPageResult:
    page_number: int
    text: str
    character_count: int
    worker_rss_mb: float
    worker_peak_rss_mb: float
    worker_baseline_rss_mb: float
    engine_initialized_rss_mb: float
    page_rendered_rss_mb: float
    initialized: bool
    engine_version: str
    container_peak_rss_mb: float = 0.0


class RapidOCREngine:
    """In-process adapter retained only for injected tests and explicit tooling."""

    def __init__(self, *, threads: int = 1) -> None:
        try:
            from rapidocr import RapidOCR

            self._engine = RapidOCR(params=_rapidocr_params(threads))
        except Exception as exc:
            raise OCRUnavailableError("检测到扫描版 PDF，需要启用 OCR。") from exc

    @property
    def version(self) -> str:
        return _rapidocr_version()

    def recognize(self, image: bytes) -> str:
        result = self._engine(image)
        texts = getattr(result, "txts", None) or ()
        return "\n".join(str(text).strip() for text in texts if str(text).strip())


class OCRWorkerClient:
    def __init__(
        self,
        *,
        max_rss_mb: int = 300,
        max_pages: int = 1,
        container_memory_budget_mb: int = 480,
        threads: int = 1,
        timeout_seconds: int = 180,
    ) -> None:
        if min(max_rss_mb, max_pages, container_memory_budget_mb, threads, timeout_seconds) <= 0:
            raise ValueError("OCR worker limits must be positive")
        self._max_rss_mb = max_rss_mb
        self._max_pages = max_pages
        self._container_memory_budget_mb = container_memory_budget_mb
        self._threads = threads
        self._timeout_seconds = timeout_seconds
        self._connection: Connection | None = None
        self._process = None
        self._processed_pages = 0

    @property
    def initialized(self) -> bool:
        return bool(self._process and self._process.is_alive())

    def recognize_page(self, path: Path, page_number: int, dpi: int) -> OCRPageResult:
        self._ensure_started(page_number)
        assert self._connection is not None
        assert self._process is not None
        peak_rss = 0.0
        container_peak_rss = 0.0
        last_stage = "worker_started"
        try:
            self._connection.send({
                "command": "recognize",
                "path": str(path),
                "page_number": page_number,
                "dpi": dpi,
            })
        except (BrokenPipeError, EOFError, OSError) as exc:
            self._raise_resource_failure(
                page_number,
                reason="send_failed",
                peak_rss=peak_rss,
                container_peak_rss=container_peak_rss,
                last_stage=last_stage,
                broken_pipe=True,
                cause=exc,
            )

        deadline = time.monotonic() + self._timeout_seconds
        threshold_exceeded = False
        recycle_rss_mb = self._max_rss_mb * 0.9
        recycle_container_mb = self._container_memory_budget_mb * 0.9
        payload: dict[str, object] | None = None
        while time.monotonic() < deadline:
            rss = process_rss_mb(self._process.pid)
            peak_rss = max(peak_rss, rss)
            current_container_rss = container_memory_mb()
            if current_container_rss <= 0:
                current_container_rss = process_rss_mb() + rss
            container_peak_rss = max(container_peak_rss, current_container_rss)
            threshold_exceeded = threshold_exceeded or rss >= recycle_rss_mb or current_container_rss >= recycle_container_mb
            if current_container_rss >= self._container_memory_budget_mb:
                self._raise_resource_failure(
                    page_number,
                    reason="container_memory_limit",
                    peak_rss=peak_rss,
                    container_peak_rss=container_peak_rss,
                    last_stage=last_stage,
                )
            if self._connection.poll(0.1):
                try:
                    received = self._connection.recv()
                except (EOFError, OSError) as exc:
                    self._raise_resource_failure(
                        page_number,
                        reason="receive_failed",
                        peak_rss=peak_rss,
                        container_peak_rss=container_peak_rss,
                        last_stage=last_stage,
                        broken_pipe=True,
                        cause=exc,
                    )
                if received.get("event") == "stage":
                    last_stage = str(received.get("stage") or "unknown")[:64]
                    peak_rss = max(peak_rss, float(received.get("rss_mb") or 0.0))
                    continue
                payload = received
                break
            if not self._process.is_alive():
                self._raise_resource_failure(
                    page_number,
                    reason="worker_exited",
                    peak_rss=peak_rss,
                    container_peak_rss=container_peak_rss,
                    last_stage=last_stage,
                )
        if payload is None:
            self._raise_resource_failure(
                page_number,
                reason="timeout",
                peak_rss=peak_rss,
                container_peak_rss=container_peak_rss,
                last_stage=last_stage,
                timeout=True,
            )

        log_ocr_memory("worker_peak_observed", page_number, True, rss_mb=peak_rss)
        if not bool(payload.get("ok")):
            error = str(payload.get("error") or "OCR 子进程返回未知错误")[:300]
            self.close(force=True)
            raise OCRWorkerError(error)

        self._processed_pages += 1
        result = OCRPageResult(
            page_number=int(payload["page_number"]),
            text=str(payload.get("text") or ""),
            character_count=int(payload.get("character_count") or 0),
            worker_rss_mb=float(payload.get("rss_mb") or 0.0),
            worker_peak_rss_mb=peak_rss,
            worker_baseline_rss_mb=float(payload.get("baseline_rss_mb") or 0.0),
            engine_initialized_rss_mb=float(payload.get("engine_rss_mb") or 0.0),
            page_rendered_rss_mb=float(payload.get("render_rss_mb") or 0.0),
            initialized=bool(payload.get("initialized")),
            engine_version=str(payload.get("engine_version") or "unknown"),
            container_peak_rss_mb=container_peak_rss,
        )
        if (
            threshold_exceeded
            or self._processed_pages >= self._max_pages
            or bool(payload.get("retire_after_page"))
        ):
            self.close()
        return result

    def _raise_resource_failure(
        self,
        page_number: int,
        *,
        reason: str,
        peak_rss: float,
        container_peak_rss: float,
        last_stage: str,
        timeout: bool = False,
        broken_pipe: bool = False,
        cause: BaseException | None = None,
    ) -> None:
        process = self._process
        if process is not None and not process.is_alive():
            process.join(timeout=0.2)
        exit_code = process.exitcode if process is not None else None
        _WORKER_LOGGER.error(
            "ocr_worker_failure page=%d reason=%s exit_code=%s timeout=%s broken_pipe=%s "
            "last_stage=%s peak_rss_mb=%.1f container_peak_rss_mb=%.1f",
            page_number,
            reason,
            exit_code,
            str(timeout).lower(),
            str(broken_pipe).lower(),
            last_stage,
            peak_rss,
            container_peak_rss,
        )
        self.close(force=True)
        error = OCRWorkerResourceError(f"OCR 子进程资源异常（reason={reason}）")
        if cause is None:
            raise error
        raise error from cause

    def close(self, *, force: bool = False) -> None:
        connection, process = self._connection, self._process
        self._connection = None
        self._process = None
        self._processed_pages = 0
        if connection is not None:
            if not force and process is not None and process.is_alive():
                try:
                    connection.send({"command": "shutdown"})
                except (BrokenPipeError, EOFError, OSError):
                    pass
            connection.close()
        if process is not None:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=2)

    def _ensure_started(self, page_number: int) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self.close(force=True)
        parent_before_mb = process_rss_mb()
        container_before_mb = container_memory_mb()
        release_process_memory()
        parent_after_mb = process_rss_mb()
        container_after_mb = container_memory_mb()
        _WORKER_LOGGER.info(
            "ocr_parent_cleanup page=%d parent_rss_before_mb=%.1f "
            "parent_rss_after_mb=%.1f container_before_mb=%.1f container_after_mb=%.1f",
            page_number,
            parent_before_mb,
            parent_after_mb,
            container_before_mb,
            container_after_mb,
        )
        log_ocr_memory("before_worker_spawn", page_number, False, rss_mb=parent_after_mb)
        context = get_context("spawn")
        parent_connection, child_connection = context.Pipe()
        process = context.Process(
            target=_rapidocr_worker,
            args=(child_connection, self._max_rss_mb, self._threads),
            name="revia-ocr-worker",
            daemon=True,
        )
        process.start()
        child_connection.close()
        self._connection = parent_connection
        self._process = process
        self._processed_pages = 0


def _rapidocr_worker(connection: Connection, max_rss_mb: int, threads: int) -> None:
    _configure_worker_threads(threads)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = None
    initialized = False
    engine_version = "unknown"
    try:
        while True:
            message = connection.recv()
            if message.get("command") == "shutdown":
                break
            if message.get("command") != "recognize":
                continue
            page_number = int(message["page_number"])
            path = Path(str(message["path"]))
            dpi = int(message["dpi"])
            try:
                baseline_rss_mb = _report_worker_stage(
                    connection, "worker_received_page", page_number, initialized
                )
                if engine is None:
                    _report_worker_stage(connection, "worker_engine_initializing", page_number, False)
                    from rapidocr import RapidOCR

                    engine = RapidOCR(params=_rapidocr_params(threads))
                    initialized = True
                    engine_version = _rapidocr_version()
                    engine_rss_mb = _report_worker_stage(
                        connection, "worker_engine_initialized", page_number, True
                    )
                else:
                    engine_rss_mb = process_rss_mb()

                import fitz

                lines: list[str] = []
                render_rss_mb = engine_rss_mb
                _report_worker_stage(connection, "worker_pdf_opening", page_number, True)
                document = fitz.open(path)
                page = document.load_page(page_number - 1)
                _report_worker_stage(connection, "worker_pdf_opened", page_number, True)
                try:
                    requested_scale = dpi / 72.0
                    scale = min(requested_scale, 1400 / max(1.0, page.rect.width))
                    matrix = fitz.Matrix(scale, scale)
                    band_height_points = 600 / scale
                    overlap_points = 24 / scale
                    top = 0.0
                    while top < page.rect.height:
                        bottom = min(page.rect.height, top + band_height_points)
                        clip = fitz.Rect(page.rect.x0, top, page.rect.x1, bottom)
                        _report_worker_stage(connection, "worker_page_rendering", page_number, True)
                        pixmap = page.get_pixmap(
                            matrix=matrix,
                            clip=clip,
                            colorspace=fitz.csGRAY,
                            alpha=False,
                        )
                        tile_bytes = pixmap.tobytes("png")
                        del pixmap
                        render_rss_mb = max(
                            render_rss_mb,
                            _report_worker_stage(
                                connection, "worker_page_rendered", page_number, True
                            ),
                        )
                        _report_worker_stage(connection, "worker_tile_inference", page_number, True)
                        result = engine(tile_bytes)
                        texts = getattr(result, "txts", None) or ()
                        for value in texts:
                            line = str(value).strip()
                            if line and (not lines or line != lines[-1]):
                                lines.append(line)
                        del texts
                        del result
                        del tile_bytes
                        gc.collect()
                        _report_worker_stage(connection, "worker_tile_completed", page_number, True)
                        if bottom >= page.rect.height:
                            break
                        top = bottom - overlap_points
                finally:
                    page = None
                    document.close()
                    del document
                text = "\n".join(lines)
                del lines
                gc.collect()
                rss_mb = _report_worker_stage(connection, "worker_page_completed", page_number, True)
                connection.send({
                    "event": "result",
                    "ok": True,
                    "page_number": page_number,
                    "text": text,
                    "character_count": len(text),
                    "rss_mb": rss_mb,
                    "baseline_rss_mb": baseline_rss_mb,
                    "engine_rss_mb": engine_rss_mb,
                    "render_rss_mb": render_rss_mb,
                    "initialized": True,
                    "engine_version": engine_version,
                    "retire_after_page": rss_mb >= max_rss_mb * 0.9,
                })
                if rss_mb >= max_rss_mb * 0.9:
                    break
            except Exception as exc:
                connection.send({
                    "event": "result",
                    "ok": False,
                    "page_number": page_number,
                    "error": (str(exc).strip() or exc.__class__.__name__)[:300],
                })
                break
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        engine = None
        gc.collect()
        connection.close()


def _report_worker_stage(
    connection: Connection,
    stage: str,
    page_number: int,
    initialized: bool,
) -> float:
    rss_mb = log_ocr_memory(stage, page_number, initialized)
    connection.send({
        "event": "stage",
        "stage": stage,
        "page_number": page_number,
        "rss_mb": rss_mb,
    })
    return rss_mb


def _configure_worker_threads(threads: int) -> None:
    value = str(max(1, threads))
    for name in (
        "OMP_NUM_THREADS",
        "OMP_THREAD_LIMIT",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "ORT_NUM_THREADS",
    ):
        os.environ[name] = value


def _rapidocr_params(threads: int) -> dict[str, object]:
    return {
        "Global.log_level": "error",
        "Global.max_side_len": 1024,
        "Det.limit_type": "max",
        "Det.limit_side_len": 1024,
        "Cls.cls_batch_num": 1,
        "Rec.rec_batch_num": 1,
        "EngineConfig.onnxruntime.intra_op_num_threads": max(1, threads),
        "EngineConfig.onnxruntime.inter_op_num_threads": max(1, threads),
        "EngineConfig.onnxruntime.enable_cpu_mem_arena": False,
    }


def _rapidocr_version() -> str:
    try:
        return version("rapidocr")
    except PackageNotFoundError:
        return "unknown"
