from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import fitz

from app.document.ocr import OCRUnavailableError, RapidOCREngine


class PDFParsingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedPageData:
    page_number: int
    text: str


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
                if not document.is_pdf:
                    raise PDFParsingError("Uploaded file is not a PDF")
                if document.page_count > self._max_pages:
                    raise PDFParsingError(f"PDF 页数不能超过 {self._max_pages} 页")
                extracted = [self._normalize_text(page.get_text("text", sort=True)) for page in document]
                image_pages = [bool(page.get_images(full=True)) for page in document]
                valid_text_pages = sum(len(text) >= self._minimum_text_length for text in extracted)
                is_scanned = valid_text_pages == 0 and any(image_pages)
                pages: list[ParsedPageData] = []
                ocr_page_count = 0
                ocr_failures: list[str] = []
                for index, page in enumerate(document):
                    text = extracted[index]
                    needs_ocr = len(text) < self._minimum_text_length and image_pages[index]
                    if needs_ocr:
                        if not self._ocr_enabled:
                            raise PDFParsingError("检测到扫描版 PDF，需要启用 OCR。")
                        try:
                            ocr_page_count += 1
                            text = self._ocr_page(page)
                        except OCRUnavailableError as exc:
                            raise PDFParsingError("检测到扫描版 PDF，需要启用 OCR。") from exc
                        except Exception as exc:
                            ocr_failures.append(f"第 {index + 1} 页：{self._safe_error(exc)}")
                    pages.append(ParsedPageData(page_number=index + 1, text=text))

                ocr_executed = ocr_page_count > 0
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
