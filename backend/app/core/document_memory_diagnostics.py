from __future__ import annotations

import ctypes
import gc
import logging
import sys
from collections import Counter

from sqlalchemy.orm import Session

from app.core.memory import process_rss_mb


_LOGGER = logging.getLogger("revia.documents.memory")
_CHECKPOINT_INTERVAL = 10
_IDENTITY_TYPES = (
    "Document",
    "DocumentPage",
    "ParsedDocument",
    "ParsedPage",
    "TextChunk",
)


class DocumentMemoryDiagnostics:
    def __init__(self, session: Session, *, enabled: bool = False) -> None:
        self._session: Session | None = session
        self._enabled = enabled

    def start(self) -> None:
        """Keep the hook for callers without starting allocation tracing."""

    def page_completed(self, page_number: int, total_pages: int) -> None:
        session = self._session
        if not self._enabled or session is None:
            return
        _LOGGER.info(
            "document_parent_memory stage=page_completed page=%d total_pages=%d rss_mb=%.1f",
            page_number,
            total_pages,
            process_rss_mb(),
        )
        if page_number % _CHECKPOINT_INTERVAL != 0:
            return

        gc_counts = gc.get_count()
        identity_counts = Counter(type(item).__name__ for item in session.identity_map.values())
        _LOGGER.info(
            "document_parent_memory stage=checkpoint page=%d total_pages=%d "
            "gc_gen0=%d gc_gen1=%d gc_gen2=%d identity_map=%d "
            "identity_document=%d identity_document_page=%d identity_parsed_document=%d "
            "identity_parsed_page=%d identity_text_chunk=%d",
            page_number,
            total_pages,
            gc_counts[0],
            gc_counts[1],
            gc_counts[2],
            len(session.identity_map),
            identity_counts["Document"],
            identity_counts["DocumentPage"],
            identity_counts["ParsedDocument"],
            identity_counts["ParsedPage"],
            identity_counts["TextChunk"],
        )

    def close(self) -> None:
        self._session = None
        self._enabled = False
        release_process_memory()


def release_process_memory() -> None:
    """Best-effort release of Python and glibc free memory after a document task."""
    gc.collect()
    if not sys.platform.startswith("linux"):
        return
    try:
        malloc_trim = ctypes.CDLL(None).malloc_trim
        malloc_trim.argtypes = [ctypes.c_size_t]
        malloc_trim.restype = ctypes.c_int
        malloc_trim(0)
    except Exception:
        # Non-glibc Linux images may not expose malloc_trim. Cleanup must never
        # change the durable task result.
        return

