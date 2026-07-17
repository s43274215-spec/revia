import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import fitz

from app.document.ocr import (
    OCRUnavailableError,
    OCRWorkerClient,
    OCRWorkerError,
    OCRWorkerResourceError,
)
from app.models.enums import ExtractionMethod


_LOGGER = logging.getLogger("revia.documents")


class PDFParsingError(RuntimeError):
    pass


class OCRResourceLimitedError(PDFParsingError):
    pass


class OCRDisabledError(OCRResourceLimitedError):
    pass


@dataclass(frozen=True)
class ParsedPageData:
    page_number: int
    text: str


@dataclass(frozen=True)
class ExtractedPageData:
    page_number: int
    text: str
    extraction_method: ExtractionMethod


@dataclass(frozen=True)
class ParsedPDF:
    page_count: int
    pages: list[ParsedPageData]
    parser_name: str
    parser_version: str
    is_scanned: bool
    ocr_executed: bool
    ocr_page_count: int
    ocr_error: str | None

    @property
    def raw_text(self) -> str:
        return "\n\n".join(f"[第 {page.page_number} 页]\n{page.text}" for page in self.pages)


class PDFParser:
    def __init__(
        self,
        *,
        ocr_enabled: bool = True,
        ocr_dpi: int = 180,
        minimum_text_length: int = 8,
        max_pages: int = 120,
        ocr_engine: "OCREngine | None" = None,
        ocr_worker: OCRWorkerClient | None = None,
        ocr_worker_max_rss_mb: int = 300,
        ocr_worker_threads: int = 1,
        ocr_worker_timeout_seconds: int = 180,
    ) -> None:
        self._ocr_enabled = ocr_enabled
        self._ocr_dpi = ocr_dpi
        self._minimum_text_length = minimum_text_length
        self._max_pages = max_pages
        self._ocr_engine = ocr_engine
        self._ocr_worker = ocr_worker
        self._ocr_worker_max_rss_mb = ocr_worker_max_rss_mb
        self._ocr_worker_threads = ocr_worker_threads
        self._ocr_worker_timeout_seconds = ocr_worker_timeout_seconds
        self._ocr_version: str | None = None

    @property
    def requires_page_for_ocr(self) -> bool:
        return self._ocr_engine is not None

    def parse(self, path: Path) -> ParsedPDF:
        try:
            with fitz.open(path) as document:
                self.validate_document(document)
                pages: list[ParsedPageData] = []
                ocr_page_count = 0
                ocr_failures: list[str] = []
                for index, page in enumerate(document):
                    try:
                        extracted_page = self.extract_page(page, index + 1, source_path=path)
                        if extracted_page.extraction_method == ExtractionMethod.OCR:
                            ocr_page_count += 1
                        pages.append(ParsedPageData(page_number=index + 1, text=extracted_page.text))
                    except PDFParsingError as exc:
                        ocr_failures.append(f"第 {index + 1} 页：{self._safe_error(exc)}")
                        raise

                ocr_executed = ocr_page_count > 0
                is_scanned = ocr_page_count == document.page_count and document.page_count > 0
                if is_scanned and not any(page.text.strip() for page in pages):
                    detail = "；".join(ocr_failures[:5]) or "OCR 未识别到有效文本"
                    raise PDFParsingError(f"扫描版 PDF OCR 失败：{detail}")
                engine_version = self._ocr_version or (
                    self._ocr_engine.version if ocr_executed and self._ocr_engine else None
                )
                return ParsedPDF(
                    page_count=document.page_count,
                    pages=pages,
                    parser_name="pymupdf+rapidocr" if ocr_executed else "pymupdf",
                    parser_version=(
                        f"PyMuPDF {fitz.VersionBind}; RapidOCR {engine_version}"
                        if engine_version else fitz.VersionBind
                    ),
                    is_scanned=is_scanned,
                    ocr_executed=ocr_executed,
                    ocr_page_count=ocr_page_count,
                    ocr_error="；".join(ocr_failures) if ocr_failures else None,
                )
        except PDFParsingError:
            raise
        except Exception as exc:
            raise PDFParsingError(f"Failed to parse PDF: {path.name}") from exc
        finally:
            self.close()

    def validate_document(self, document: fitz.Document) -> int:
        if not document.is_pdf:
            raise PDFParsingError("上传的文件不是有效 PDF")
        if document.page_count > self._max_pages:
            raise PDFParsingError(f"PDF 页数不能超过 {self._max_pages} 页")
        return document.page_count

    def inspect(self, path: Path) -> int:
        try:
            with fitz.open(path) as document:
                return self.validate_document(document)
        except PDFParsingError:
            raise
        except Exception as exc:
            raise PDFParsingError(f"无法打开 PDF：{path.name}") from exc

    def extract_page(
        self,
        page: fitz.Page,
        page_number: int,
        *,
        source_path: Path | None = None,
        allow_ocr: bool = True,
    ) -> ExtractedPageData:
        text_page = self.extract_text_page(page, page_number)
        if text_page is not None:
            return text_page
        return self.extract_ocr_page(
            page_number,
            source_path=source_path,
            allow_ocr=allow_ocr,
            page=page,
        )

    def extract_text_page(
        self,
        page: fitz.Page,
        page_number: int,
    ) -> ExtractedPageData | None:
        try:
            text = self._normalize_text(page.get_text("text", sort=True))
        except Exception as exc:
            _LOGGER.warning(
                "pdf_text_layer_failed page=%d error_type=%s fallback=ocr",
                page_number,
                exc.__class__.__name__,
            )
            return None
        has_raster_content = self._has_raster_content(page)
        minimum_raster_text_length = max(self._minimum_text_length, 32)
        needs_ocr = has_raster_content and len(text) < minimum_raster_text_length
        if not needs_ocr:
            return ExtractedPageData(page_number, text, ExtractionMethod.TEXT)
        return None

    @staticmethod
    def _has_raster_content(page: fitz.Page) -> bool:
        try:
            if page.get_image_info():
                return True
        except (AttributeError, RuntimeError, ValueError):
            pass
        try:
            return bool(page.get_images(full=True))
        except (AttributeError, RuntimeError, ValueError):
            return False

    def extract_ocr_page(
        self,
        page_number: int,
        *,
        source_path: Path | None,
        allow_ocr: bool = True,
        page: fitz.Page | None = None,
    ) -> ExtractedPageData:
        if not self._ocr_enabled:
            raise OCRDisabledError("ocr_disabled")
        if not allow_ocr:
            raise OCRResourceLimitedError("OCR 处理因服务器资源不足暂停，系统稍后可从当前页继续。")
        try:
            return ExtractedPageData(
                page_number,
                self._ocr_page(page, source_path, page_number),
                ExtractionMethod.OCR,
            )
        except OCRUnavailableError as exc:
            raise PDFParsingError("检测到扫描版 PDF，需要启用 OCR。") from exc
        except OCRWorkerResourceError as exc:
            raise OCRResourceLimitedError("OCR 处理因服务器资源不足暂停，系统稍后可从当前页继续。") from exc
        except OCRWorkerError as exc:
            raise PDFParsingError(f"第 {page_number} 页 OCR 失败：{self._safe_error(exc)}") from exc
        except Exception as exc:
            raise PDFParsingError(f"第 {page_number} 页 OCR 失败：{self._safe_error(exc)}") from exc

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    def _ocr_page(self, page: fitz.Page | None, source_path: Path | None, page_number: int) -> str:
        if self._ocr_engine is not None:
            if page is None:
                raise OCRWorkerError("进程内 OCR 缺少 PDF 页面")
            pixmap = page.get_pixmap(dpi=self._ocr_dpi, colorspace=fitz.csGRAY, alpha=False)
            image: bytes | None = None
            try:
                image = pixmap.tobytes("png")
                return self._normalize_text(self._ocr_engine.recognize(image))
            finally:
                image = None
                del pixmap
        if source_path is None:
            raise OCRWorkerError("OCR 子进程缺少 PDF 临时路径")
        result = self._get_ocr_worker().recognize_page(source_path, page_number, self._ocr_dpi)
        self._ocr_version = result.engine_version
        return self._normalize_text(result.text)

    def _get_ocr_worker(self) -> OCRWorkerClient:
        if self._ocr_worker is None:
            self._ocr_worker = OCRWorkerClient(
                max_rss_mb=self._ocr_worker_max_rss_mb,
                threads=self._ocr_worker_threads,
                timeout_seconds=self._ocr_worker_timeout_seconds,
            )
        return self._ocr_worker

    def close(self) -> None:
        if self._ocr_worker is not None:
            self._ocr_worker.close()
            self._ocr_worker = None

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return (str(exc).strip() or exc.__class__.__name__)[:300]


class OCREngine(Protocol):
    @property
    def version(self) -> str: ...

    def recognize(self, image: bytes) -> str: ...
