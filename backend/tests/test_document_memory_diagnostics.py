import gc
import logging
import tracemalloc
import unittest
from unittest.mock import patch

from app.core.config import Settings
from app.core.document_memory_diagnostics import DocumentMemoryDiagnostics, release_process_memory


class _FakeSession:
    def __init__(self) -> None:
        document_page = type("DocumentPage", (), {})()
        text_chunk = type("TextChunk", (), {})()
        self.identity_map = {"page": document_page, "chunk": text_chunk}


class DocumentMemoryDiagnosticsTests(unittest.TestCase):
    def test_default_configuration_disables_memory_diagnostics(self) -> None:
        self.assertFalse(Settings.model_fields["document_memory_diagnostics_enabled"].default)

    def test_enabled_diagnostics_never_start_tracemalloc_or_create_snapshots(self) -> None:
        diagnostics = DocumentMemoryDiagnostics(_FakeSession(), enabled=True)  # type: ignore[arg-type]
        with (
            patch.object(tracemalloc, "start") as start,
            patch.object(tracemalloc, "take_snapshot") as take_snapshot,
        ):
            diagnostics.start()
            diagnostics.page_completed(10, 100)
            diagnostics.close()
        start.assert_not_called()
        take_snapshot.assert_not_called()

    def test_logs_page_rss_gc_and_identity_counts_without_allocation_details(self) -> None:
        diagnostics = DocumentMemoryDiagnostics(_FakeSession(), enabled=True)  # type: ignore[arg-type]
        with self.assertLogs("revia.documents.memory", level=logging.INFO) as captured:
            diagnostics.start()
            try:
                for page_number in range(1, 11):
                    diagnostics.page_completed(page_number, 100)
            finally:
                diagnostics.close()

        logs = "\n".join(captured.output)
        self.assertEqual(logs.count("stage=page_completed"), 10)
        self.assertIn("stage=checkpoint page=10 total_pages=100", logs)
        self.assertIn("gc_gen0=", logs)
        self.assertIn("identity_map=2", logs)
        self.assertIn("identity_document_page=1", logs)
        self.assertIn("identity_text_chunk=1", logs)
        self.assertNotIn("tracemalloc", logs)
        self.assertNotIn("traced_current", logs)
        self.assertNotIn("PDF", logs)
        self.assertNotIn("api_key", logs.casefold())

    def test_close_clears_session_reference_and_releases_memory(self) -> None:
        diagnostics = DocumentMemoryDiagnostics(_FakeSession(), enabled=True)  # type: ignore[arg-type]
        with patch("app.core.document_memory_diagnostics.release_process_memory") as release:
            diagnostics.close()
        release.assert_called_once_with()
        self.assertIsNone(diagnostics._session)

    def test_memory_release_ignores_unavailable_malloc_trim(self) -> None:
        with (
            patch.object(gc, "collect") as collect,
            patch("app.core.document_memory_diagnostics.sys.platform", "linux"),
            patch("app.core.document_memory_diagnostics.ctypes.CDLL", side_effect=OSError("unavailable")),
        ):
            release_process_memory()
        collect.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
