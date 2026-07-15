from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import fitz

from app.document.ocr import OCRUnavailableError, RapidOCREngine
from app.models.enums import ExtractionMethod


class PDFParsingError(RuntimeError):
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
    ) -> None:
        self._ocr_enabled = ocr_enabled
        self._ocr_dpi = ocr_dpi
        self._minimum_text_length = minimum_text_length
        self._max_pages = max_pages
        self._ocr_engine = ocr_engine

    def parse(self, path: Path) -> ParsedPDF:
        try:
            with fitz.open(path) as document:
                self.validate_document(document)
                pages: list[ParsedPageData] = []
                ocr_page_count = 0
                ocr_failures: list[str] = []
                for index, page in enumerate(document):
                    try:
                        extracted_page = self.extract_page(page, index + 1)
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
                engine_version = self._ocr_engine.version if ocr_executed and self._ocr_engine else None
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

    def extract_page(self, page: fitz.Page, page_number: int) -> ExtractedPageData:
        text = self._normalize_text(page.get_text("text", sort=True))
        needs_ocr = len(text) < self._minimum_text_length and bool(page.get_images(full=True))
        if not needs_ocr:
            return ExtractedPageData(page_number, text, ExtractionMethod.TEXT)
        if not self._ocr_enabled:
            raise PDFParsingError("检测到扫描版 PDF，需要启用 OCR。")
        try:
            return ExtractedPageData(page_number, self._ocr_page(page), ExtractionMethod.OCR)
        except OCRUnavailableError as exc:
            raise PDFParsingError("检测到扫描版 PDF，需要启用 OCR。") from exc
        except Exception as exc:
            raise PDFParsingError(f"第 {page_number} 页 OCR 失败：{self._safe_error(exc)}") from exc

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    def _ocr_page(self, page: fitz.Page) -> str:
        engine = self._get_ocr_engine()
        pixmap = page.get_pixmap(dpi=self._ocr_dpi, colorspace=fitz.csRGB, alpha=False)
        image: bytes | None = None
        try:
            image = pixmap.tobytes("png")
            return self._normalize_text(engine.recognize(image))
        finally:
            image = None
            del pixmap

    def _get_ocr_engine(self) -> "OCREngine":
        if self._ocr_engine is None:
            self._ocr_engine = RapidOCREngine()
        return self._ocr_engine

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return (str(exc).strip() or exc.__class__.__name__)[:300]


class OCREngine(Protocol):
    @property
    def version(self) -> str: ...

    def recognize(self, image: bytes) -> str: ...
