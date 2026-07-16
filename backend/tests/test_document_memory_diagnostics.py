import logging
import unittest

from app.core.document_memory_diagnostics import DocumentMemoryDiagnostics


class _FakeSession:
    def __init__(self) -> None:
        document_page = type("DocumentPage", (), {})()
        text_chunk = type("TextChunk", (), {})()
        self.identity_map = {"page": document_page, "chunk": text_chunk}


class DocumentMemoryDiagnosticsTests(unittest.TestCase):
    def test_logs_page_rss_checkpoint_identity_counts_and_safe_growth(self) -> None:
        diagnostics = DocumentMemoryDiagnostics(_FakeSession(), enabled=True)  # type: ignore[arg-type]
        retained: list[bytes] = []
        with self.assertLogs("revia.documents.memory", level=logging.INFO) as captured:
            diagnostics.start()
            try:
                for page_number in range(1, 11):
                    retained.append(bytes(1024 * page_number))
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
        self.assertIn("document_parent_tracemalloc page=10", logs)
        self.assertNotIn("PDF", logs)
        self.assertNotIn("api_key", logs.casefold())


if __name__ == "__main__":
    unittest.main()
