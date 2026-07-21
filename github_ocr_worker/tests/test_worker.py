from __future__ import annotations
import unittest
import fitz
from github_ocr_worker.worker import has_raster, normalize, outline, safe_error

class WorkerHelpersTests(unittest.TestCase):
    def test_text_normalization(self):
        self.assertEqual(normalize(" a\r\nb\r "), "a\nb")

    def test_editable_page_is_not_misclassified_as_raster(self):
        pdf=fitz.open(); page=pdf.new_page(); page.insert_text((72,72),"editable text")
        self.assertFalse(has_raster(page)); pdf.close()

    def test_signed_download_url_is_never_returned_in_error_text(self):
        request = __import__("httpx").Request("GET", "https://signed.example/file?secret=abc")
        response = __import__("httpx").Response(403, request=request)
        error = __import__("httpx").HTTPStatusError("failed signed URL", request=request, response=response)
        self.assertEqual(safe_error(error), "PDF 下载失败（status=403）")

    def test_outline_is_sanitized(self):
        pdf=fitz.open(); pdf.new_page(); pdf.set_toc([[1,"  Chapter   One  ",1]])
        self.assertEqual(outline(pdf), [{"level":1,"title":"Chapter One","page_number":1}]); pdf.close()

if __name__ == "__main__": unittest.main()
