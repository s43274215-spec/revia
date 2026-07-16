from __future__ import annotations

import gc
import logging
import tracemalloc
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.memory import process_rss_mb


_LOGGER = logging.getLogger("revia.documents.memory")
_CHECKPOINT_INTERVAL = 10
_TOP_GROWTH_LIMIT = 20
_IDENTITY_TYPES = (
    "Document",
    "DocumentPage",
    "ParsedDocument",
    "ParsedPage",
    "TextChunk",
)


class DocumentMemoryDiagnostics:
    def __init__(self, session: Session, *, enabled: bool = False) -> None:
        self._session = session
        self._enabled = enabled
        self._owns_tracing = False
        self._previous_snapshot: tracemalloc.Snapshot | None = None

    def start(self) -> None:
        if not self._enabled:
            return
        if not tracemalloc.is_tracing():
            tracemalloc.start(1)
            self._owns_tracing = True
        self._previous_snapshot = tracemalloc.take_snapshot()

    def page_completed(self, page_number: int, total_pages: int) -> None:
        if not self._enabled:
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
        identity_counts = Counter(type(item).__name__ for item in self._session.identity_map.values())
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        _LOGGER.info(
            "document_parent_memory stage=checkpoint page=%d total_pages=%d "
            "gc_gen0=%d gc_gen1=%d gc_gen2=%d identity_map=%d "
            "identity_document=%d identity_document_page=%d identity_parsed_document=%d "
            "identity_parsed_page=%d identity_text_chunk=%d traced_current_kb=%.1f traced_peak_kb=%.1f",
            page_number,
            total_pages,
            gc_counts[0],
            gc_counts[1],
            gc_counts[2],
            len(self._session.identity_map),
            identity_counts["Document"],
            identity_counts["DocumentPage"],
            identity_counts["ParsedDocument"],
            identity_counts["ParsedPage"],
            identity_counts["TextChunk"],
            traced_current / 1024,
            traced_peak / 1024,
        )
        self._log_tracemalloc_growth(page_number)

    def close(self) -> None:
        self._previous_snapshot = None
        if self._owns_tracing and tracemalloc.is_tracing():
            tracemalloc.stop()
        self._owns_tracing = False

    def _log_tracemalloc_growth(self, page_number: int) -> None:
        current_snapshot = tracemalloc.take_snapshot()
        previous_snapshot = self._previous_snapshot
        self._previous_snapshot = current_snapshot
        if previous_snapshot is None:
            return
        growth = [
            statistic
            for statistic in current_snapshot.compare_to(previous_snapshot, "lineno")
            if statistic.size_diff > 0
        ][:_TOP_GROWTH_LIMIT]
        for rank, statistic in enumerate(growth, start=1):
            frame = statistic.traceback[0]
            _LOGGER.info(
                "document_parent_tracemalloc page=%d rank=%d location=%s:%d "
                "size_diff_kb=%.1f count_diff=%d",
                page_number,
                rank,
                Path(frame.filename).name,
                frame.lineno,
                statistic.size_diff / 1024,
                statistic.count_diff,
            )

