import asyncio
import json
import tempfile
import unittest
import uuid
from io import BytesIO
from pathlib import Path

import fitz
from fastapi.testclient import TestClient
from fastapi import UploadFile
from PIL import Image, ImageDraw
from starlette.datastructures import Headers
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every ORM model
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.document.parser import PDFParser, PDFParsingError
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.document.splitter import StructuredTextSplitter
from app.document.structure import BlockKind, StructuredText, TextBlock
from app.main import app
from app.models.document import ParsedDocument, ParsedPage, TextChunk
from app.models.project import Document, Project
from app.models.enums import DocumentKind, DocumentProcessingStatus
from app.models.workspace import Workspace
from app.services.document_processing import DocumentProcessingError, DocumentProcessingService
from app.services.storage import LocalFileStorage
from tests.helpers import authorization_header


def build_test_pdf() -> bytes:
    document = fitz.open()
    first = document.new_page()
    first.insert_text((72, 72), "Chapter One\n1.1 Externality\nExternality affects third parties outside market prices.")
    second = document.new_page()
    second.insert_text((72, 72), "Public goods are non-rival and non-excludable.\nFree-riding may reduce supply.")
    content = document.tobytes()
    document.close()
    return content


def build_scanned_pdf() -> bytes:
    image = Image.new("RGB", (1200, 800), "white")
    ImageDraw.Draw(image).text((80, 100), "scanned page", fill="black")
    image_output = BytesIO()
    image.save(image_output, format="PNG")
    document = fitz.open()
    page = document.new_page(width=1200, height=800)
    page.insert_image(page.rect, stream=image_output.getvalue())
    content = document.tobytes()
    document.close()
    return content


class FakeOCREngine:
    version = "test"

    def __init__(self, text: str = "第一章 人力资源管理\n人力资本与人才管理") -> None:
        self.text = text
        self.calls = 0

    def recognize(self, image: bytes) -> str:
        self.calls += 1
        self.last_image = image
        return self.text


class PDFParserOCRTests(unittest.TestCase):
    def test_scanned_page_uses_ocr_and_preserves_existing_parser_output(self) -> None:
        engine = FakeOCREngine()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scanned.pdf"
            path.write_bytes(build_scanned_pdf())
            parsed = PDFParser(ocr_engine=engine).parse(path)
        self.assertTrue(parsed.is_scanned)
        self.assertTrue(parsed.ocr_executed)
        self.assertEqual(parsed.ocr_page_count, 1)
        self.assertIsNone(parsed.ocr_error)
        self.assertIn("人力资本", parsed.pages[0].text)
        self.assertEqual(engine.calls, 1)
        self.assertTrue(engine.last_image.startswith(b"\x89PNG"))

    def test_text_pdf_does_not_execute_ocr(self) -> None:
        engine = FakeOCREngine()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.pdf"
            path.write_bytes(build_test_pdf())
            parsed = PDFParser(ocr_engine=engine).parse(path)
        self.assertFalse(parsed.is_scanned)
        self.assertFalse(parsed.ocr_executed)
        self.assertEqual(parsed.ocr_page_count, 0)
        self.assertEqual(engine.calls, 0)

    def test_scanned_pdf_without_ocr_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scanned.pdf"
            path.write_bytes(build_scanned_pdf())
            with self.assertRaisesRegex(PDFParsingError, "检测到扫描版 PDF，需要启用 OCR"):
                PDFParser(ocr_enabled=False).parse(path)


class TextSplitterTests(unittest.TestCase):
    def test_splitter_preserves_structural_boundaries(self) -> None:
        structured = StructuredText(blocks=[
            TextBlock(BlockKind.CHAPTER, 1, "第一章 市场失灵"),
            TextBlock(BlockKind.CONTENT, 1, "外部性会使私人和社会成本产生偏离。"),
            TextBlock(BlockKind.SECTION, 2, "1.1 公共物品"),
            TextBlock(BlockKind.CONTENT, 2, "公共物品具有非竞争性和非排他性。"),
            TextBlock(BlockKind.CHAPTER, 3, "第二章 宏观政策"),
            TextBlock(BlockKind.CONTENT, 3, "财政政策通过支出与税收影响总需求。"),
        ])
        chunks = StructuredTextSplitter(target_size=60, maximum_size=100).split(structured)
        self.assertEqual(chunks[0].chapter_title, "第一章 市场失灵")
        self.assertTrue(any(chunk.section_title == "1.1 公共物品" for chunk in chunks))
        self.assertEqual(chunks[-1].chapter_title, "第二章 宏观政策")


class DocumentUploadAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.project_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        with self.Session() as session:
            session.add(Workspace(id=self.workspace_id))
            session.add(Project(
                id=self.project_id,
                workspace_id=self.workspace_id,
                name="PDF 解析测试",
                description="API test",
            ))
            session.commit()

        def override_db():
            with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = lambda: Settings(
            database_url="sqlite+pysqlite:///:memory:",
            file_storage_root=self.storage.name,
        )
        self.client = TestClient(app)
        self.client.headers.update(authorization_header(self.workspace_id))

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.storage.cleanup()

    def test_pdf_upload_parses_pages_splits_and_persists(self) -> None:
        response = self.client.post(
            f"/api/v1/projects/{self.project_id}/documents",
            data={"kind": "course_material"},
            files={"file": ("economics.pdf", build_test_pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["document"]["processing_status"], "parsed")
        self.assertEqual(payload["parsed_document"]["page_count"], 2)
        self.assertFalse(payload["parsed_document"]["is_scanned"])
        self.assertFalse(payload["parsed_document"]["ocr_executed"])
        self.assertEqual(payload["parsed_document"]["ocr_page_count"], 0)
        self.assertIsNone(payload["parsed_document"]["ocr_error"])
        self.assertEqual([page["page_number"] for page in payload["parsed_document"]["pages"]], [1, 2])
        self.assertIn("Externality", payload["parsed_document"]["pages"][0]["text"])
        self.assertGreaterEqual(len(payload["parsed_document"]["chunks"]), 1)
        self.assertIsNone(payload["document"]["storage_key"])
        self.assertEqual(list(Path(self.storage.name).rglob("*.pdf")), [])

        with self.Session() as session:
            self.assertEqual(len(session.scalars(select(ParsedDocument)).all()), 1)
            self.assertEqual(len(session.scalars(select(ParsedPage)).all()), 2)
            self.assertGreaterEqual(len(session.scalars(select(TextChunk)).all()), 1)

        result_summary = {
            "status": payload["document"]["processing_status"],
            "page_count": payload["parsed_document"]["page_count"],
            "pages": [
                {"page_number": page["page_number"], "text": page["text"][:45]}
                for page in payload["parsed_document"]["pages"]
            ],
            "chunk_count": len(payload["parsed_document"]["chunks"]),
        }
        print("PDF_UPLOAD_RESULT=" + json.dumps(result_summary, ensure_ascii=False))

    def test_scanned_upload_persists_ocr_metadata_and_chunks(self) -> None:
        engine = FakeOCREngine()
        with self.Session() as session:
            service = DocumentProcessingService(
                db=session,
                storage=LocalFileStorage(Path(self.storage.name), max_upload_bytes=25 * 1024 * 1024),
                parser=PDFParser(ocr_engine=engine),
                structurer=TextStructurer(),
                splitter=StructuredTextSplitter(),
            )
            upload = UploadFile(
                file=BytesIO(build_scanned_pdf()),
                filename="scanned.pdf",
                headers=Headers({"content-type": "application/pdf"}),
            )
            document = asyncio.run(service.process_upload(
                self.workspace_id,
                self.project_id,
                DocumentKind.COURSE_MATERIAL,
                upload,
            ))
            parsed = document.parsed_document
            self.assertIsNotNone(parsed)
            assert parsed is not None
            self.assertTrue(parsed.is_scanned)
            self.assertTrue(parsed.ocr_executed)
            self.assertEqual(parsed.ocr_page_count, 1)
            self.assertIsNone(parsed.ocr_error)
            self.assertGreaterEqual(len(parsed.chunks), 1)

    def test_scanned_upload_without_ocr_persists_clear_failure_reason(self) -> None:
        with self.Session() as session:
            service = DocumentProcessingService(
                db=session,
                storage=LocalFileStorage(Path(self.storage.name), max_upload_bytes=25 * 1024 * 1024),
                parser=PDFParser(ocr_enabled=False),
                structurer=TextStructurer(),
                splitter=StructuredTextSplitter(),
            )
            upload = UploadFile(
                file=BytesIO(build_scanned_pdf()),
                filename="scanned.pdf",
                headers=Headers({"content-type": "application/pdf"}),
            )
            with self.assertRaisesRegex(DocumentProcessingError, "检测到扫描版 PDF，需要启用 OCR"):
                asyncio.run(service.process_upload(
                    self.workspace_id,
                    self.project_id,
                    DocumentKind.COURSE_MATERIAL,
                    upload,
                ))
            failed = session.query(Document).filter_by(project_id=self.project_id).one()
            self.assertEqual(failed.processing_status, DocumentProcessingStatus.FAILED)
            self.assertIn("检测到扫描版 PDF，需要启用 OCR", failed.error_message or "")
            self.assertIsNone(failed.storage_key)
            self.assertEqual(list(Path(self.storage.name).rglob("*.pdf")), [])

    def test_upload_size_limit_returns_clear_error_and_cleans_partial_file(self) -> None:
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            database_url="sqlite+pysqlite:///:memory:",
            file_storage_root=self.storage.name,
            max_upload_mb=1,
        )
        response = self.client.post(
            f"/api/v1/projects/{self.project_id}/documents",
            data={"kind": "course_material"},
            files={"file": ("too-large.pdf", b"%PDF" + b"0" * (1024 * 1024 + 1), "application/pdf")},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("PDF 文件不能超过 1MB", response.json()["detail"])
        self.assertEqual(list(Path(self.storage.name).rglob("*.pdf")), [])

    def test_page_limit_returns_clear_error_and_cleans_pdf(self) -> None:
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            database_url="sqlite+pysqlite:///:memory:",
            file_storage_root=self.storage.name,
            max_pdf_pages=1,
        )
        response = self.client.post(
            f"/api/v1/projects/{self.project_id}/documents",
            data={"kind": "course_material"},
            files={"file": ("two-pages.pdf", build_test_pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("PDF 页数不能超过 1 页", response.json()["detail"])
        self.assertEqual(list(Path(self.storage.name).rglob("*.pdf")), [])


if __name__ == "__main__":
    unittest.main()
