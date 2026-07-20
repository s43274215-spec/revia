import tempfile
import time
import unittest
import uuid
from pathlib import Path

import fitz
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.base import Base
from app.document.parser import ExtractedPageData, PDFParser, ParsedPDF, ParsedPageData
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.models.document import DocumentPage, ParsedDocument, TextChunk
from app.models.enums import (
    DocumentKind,
    DocumentPageStatus,
    DocumentProcessingStatus,
    ExtractionMethod,
    ProjectStatus,
)
from app.models.project import Document, Project
from app.models.workspace import Workspace
from app.services.document_processing import DocumentProcessingError, DocumentProcessingService
from app.services.storage import (
    LocalStorageProvider,
    S3StorageProvider,
    StorageDownloadLimitError,
    StorageUnavailableError,
    UploadAuthorizationError,
    UploadURLSigner,
    build_object_key,
)


class FakeStreamingBody:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self._position = 0
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        chunk = self._content[self._position:self._position + size]
        self._position += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.deleted: list[str] = []
        self.bodies: list[FakeStreamingBody] = []
        self.get_error: Exception | None = None
        self.head_error: Exception | None = None
        self.head_calls = 0

    def generate_presigned_url(self, operation: str, *, Params: dict, ExpiresIn: int) -> str:
        return f"https://private-s3.example/{Params['Key']}?expires={ExpiresIn}"

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        if self.get_error is not None:
            raise self.get_error
        body = FakeStreamingBody(self.objects[Key])
        self.bodies.append(body)
        return {"ContentLength": len(self.objects[Key]), "Body": body}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, int]:
        self.head_calls += 1
        if self.head_error is not None:
            raise self.head_error
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.deleted.append(Key)
        self.objects.pop(Key, None)


class FakeS3RequestError(RuntimeError):
    response = {"Error": {"Code": "RequestTimeout"}}


class FakeS3ForbiddenError(RuntimeError):
    response = {"Error": {"Code": "403"}}


class FakeDetailedS3AccessDeniedError(RuntimeError):
    response = {
        "Error": {"Code": "AccessDenied", "Message": "application key is not authorized"},
        "ResponseMetadata": {
            "HTTPStatusCode": 403,
            "RequestId": "request-123",
            "HostId": "host-456",
        },
    }


class FakeS3DownloadCapError(RuntimeError):
    response = {
        "Error": {
            "Code": "AccessDenied",
            "Message": "Cannot download file, download bandwidth or transaction (Class B) cap exceeded.",
        },
        "ResponseMetadata": {
            "HTTPStatusCode": 403,
            "RequestId": "cap-request-123",
            "HostId": "cap-host-456",
        },
    }


def build_text_pdf(page_count: int) -> bytes:
    pdf = fitz.open()
    for page_number in range(1, page_count + 1):
        page = pdf.new_page()
        heading = "1.1 Durable Knowledge Structure\n" if page_number == 1 else ""
        page.insert_text(
            (54, 54),
            f"{heading}Page {page_number} explains durable learning content and connected concepts across pages.",
        )
    content = pdf.tobytes(garbage=4, deflate=True)
    pdf.close()
    return content


class MockOCRParser(PDFParser):
    def extract_text_page(self, page: fitz.Page, page_number: int) -> ExtractedPageData:
        return ExtractedPageData(
            page_number=page_number,
            text=f"Mock OCR page {page_number} contains scanned course material.",
            extraction_method=ExtractionMethod.OCR,
        )


class LongPDFResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage_directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.workspace_id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        with self.Session() as db:
            db.add(Workspace(id=self.workspace_id))
            db.add(Project(id=self.project_id, workspace_id=self.workspace_id, name="长文档测试"))
            db.commit()
        self.storage = LocalStorageProvider(
            Path(self.storage_directory.name),
            max_upload_bytes=200 * 1024 * 1024,
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.storage_directory.cleanup()

    def _document(self, page_count: int) -> uuid.UUID:
        document_id = uuid.uuid4()
        object_key = build_object_key(self.workspace_id, document_id)
        path = self.storage.resolve(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = build_text_pdf(page_count)
        path.write_bytes(content)
        with self.Session() as db:
            db.add(Document(
                id=document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name=f"{page_count}-pages.pdf",
                mime_type="application/pdf",
                size_bytes=len(content),
                storage_key=object_key,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.QUEUED,
                processing_phase="queued",
            ))
            db.commit()
        return document_id

    def _service(
        self,
        db,
        *,
        max_pages: int = 600,
        parser: PDFParser | None = None,
        splitter: StructuredTextSplitter | None = None,
    ) -> DocumentProcessingService:
        return DocumentProcessingService(
            db,
            self.storage,
            parser or PDFParser(max_pages=max_pages),
            TextStructurer(),
            splitter or StructuredTextSplitter(),
            max_upload_bytes=200 * 1024 * 1024,
            lease_seconds=30,
        )

    def test_600_page_text_pdf_persists_every_page_and_structured_chunks(self) -> None:
        document_id = self._document(600)
        started = time.monotonic()
        with self.Session() as db:
            document = self._service(db).process_document(document_id)
            self.assertEqual(document.processing_status, DocumentProcessingStatus.PARSED)
            self.assertEqual(document.total_pages, 600)
            self.assertEqual(document.processed_pages, 600)
            self.assertEqual(document.ocr_page_count, 0)
            pages = list(db.scalars(select(DocumentPage).where(DocumentPage.document_id == document_id)).all())
            chunks = list(
                db.scalars(
                    select(TextChunk)
                    .join(ParsedDocument, TextChunk.parsed_document_id == ParsedDocument.id)
                    .where(ParsedDocument.document_id == document_id)
                    .order_by(TextChunk.position)
                ).all()
            )
            self.assertEqual(len(pages), 600)
            self.assertTrue(all(page.status == DocumentPageStatus.COMPLETED for page in pages))
            self.assertTrue(any(chunk.page_end > chunk.page_start for chunk in chunks))
            self.assertEqual(min(chunk.page_start for chunk in chunks), 1)
            self.assertEqual(max(chunk.page_end for chunk in chunks), 600)
            self.assertTrue(all(chunk.section_title == "1.1 Durable Knowledge Structure" for chunk in chunks))
            self.assertIsNone(document.storage_key)
        self.assertFalse(any(Path(self.storage_directory.name).rglob("*.pdf")))
        print(f"LONG_PDF_600_SECONDS={time.monotonic() - started:.3f}")

    def test_interrupt_after_137_resumes_at_138_without_duplicates(self) -> None:
        document_id = self._document(200)
        wide_splitter = StructuredTextSplitter(target_size=100_000, maximum_size=120_000)
        with self.Session() as first_db:
            with self.assertRaisesRegex(DocumentProcessingError, "第 137 页后中断"):
                self._service(first_db, splitter=wide_splitter).process_document(
                    document_id, interrupt_after_page=137
                )
            interrupted = first_db.get(Document, document_id)
            assert interrupted is not None
            self.assertEqual(interrupted.processing_status, DocumentProcessingStatus.INTERRUPTED)
            self.assertEqual(interrupted.processed_pages, 137)
            self.assertEqual(interrupted.current_page, 138)
            self.assertIsNotNone(interrupted.storage_key)

        with self.Session() as restarted_db:
            service = self._service(restarted_db, splitter=wide_splitter)
            resumed = service.process_document(document_id)
            self.assertEqual(resumed.processing_status, DocumentProcessingStatus.PARSED)
            pages = list(restarted_db.scalars(
                select(DocumentPage).where(DocumentPage.document_id == document_id).order_by(DocumentPage.page_number)
            ).all())
            self.assertEqual(len(pages), 200)
            self.assertEqual(pages[0].retry_count, 0)
            self.assertEqual(pages[136].retry_count, 0)
            self.assertEqual(pages[137].retry_count, 0)
            chunks = list(restarted_db.scalars(select(TextChunk)).all())
            self.assertEqual(len(chunks), 1)
            self.assertEqual((chunks[0].page_start, chunks[0].page_end), (1, 200))

            service.process_document(document_id)
            self.assertEqual(len(list(restarted_db.scalars(select(DocumentPage)).all())), 200)
            self.assertEqual(len(list(restarted_db.scalars(select(TextChunk)).all())), 1)

    def test_mock_ocr_handles_600_pages_without_rendering_images(self) -> None:
        document_id = self._document(600)
        with self.Session() as db:
            document = self._service(db, max_pages=600, parser=MockOCRParser(max_pages=600)).process_document(document_id)
            self.assertEqual(document.processed_pages, 600)
            self.assertEqual(document.ocr_page_count, 600)
            methods = set(db.scalars(
                select(DocumentPage.extraction_method).where(DocumentPage.document_id == document_id)
            ).all())
            self.assertEqual(methods, {ExtractionMethod.OCR})

    def test_chapter_structure_crosses_processing_checkpoint_without_page_chunking(self) -> None:
        parsed = ParsedPDF(
            page_count=200,
            pages=[
                ParsedPageData(1, "第一章 人力资源管理\n章节导言"),
                ParsedPageData(137, "检查点前的连续内容。"),
                ParsedPageData(138, "检查点后的连续内容。"),
                ParsedPageData(200, "章节结尾内容。"),
            ],
            parser_name="test",
            parser_version="test",
            is_scanned=False,
            ocr_executed=False,
            ocr_page_count=0,
            ocr_error=None,
        )
        chunks = StructuredTextSplitter(target_size=10_000, maximum_size=12_000).split(
            TextStructurer().structure(parsed)
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].chapter_title, "第一章 人力资源管理")
        self.assertEqual((chunks[0].page_start, chunks[0].page_end), (1, 200))

    def test_database_lease_prevents_duplicate_workers_and_expired_url_is_rejected(self) -> None:
        document_id = self._document(2)
        with self.Session() as first_db, self.Session() as second_db:
            first = self._service(first_db)
            second = self._service(second_db)
            self.assertTrue(first.acquire_lease(document_id, "worker-one"))
            self.assertFalse(second.acquire_lease(document_id, "worker-two"))

        signer = UploadURLSigner("test-upload-signing-key-with-32-bytes")
        object_key = build_object_key(self.workspace_id, document_id)
        token = signer.issue(self.workspace_id, document_id, object_key, int(time.time()) - 1)
        with self.assertRaisesRegex(UploadAuthorizationError, "已过期"):
            signer.verify(token)

    def test_s3_presigned_target_and_successful_parse_delete_private_object(self) -> None:
        document_id = uuid.uuid4()
        object_key = build_object_key(self.workspace_id, document_id)
        objects = {object_key: build_text_pdf(3)}
        client = FakeS3Client(objects)
        s3 = S3StorageProvider.__new__(S3StorageProvider)
        s3._client = client
        s3._bucket = "private-revia"
        s3._temp_root = Path(self.storage_directory.name)
        target = s3.create_upload_url(
            object_key,
            workspace_id=self.workspace_id,
            document_id=document_id,
            content_type="application/pdf",
            expires_in=60,
        )
        self.assertTrue(target.url.startswith("https://private-s3.example/"))
        with self.Session() as db:
            db.add(Document(
                id=document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="s3.pdf",
                mime_type="application/pdf",
                size_bytes=len(objects[object_key]),
                storage_key=object_key,
                storage_backend="s3",
                processing_status=DocumentProcessingStatus.QUEUED,
                processing_phase="queued",
            ))
            db.commit()
            service = DocumentProcessingService(
                db,
                s3,
                PDFParser(max_pages=600),
                TextStructurer(),
                StructuredTextSplitter(),
                max_upload_bytes=150 * 1024 * 1024,
            )
            parsed = service.process_document(document_id)
            self.assertEqual(parsed.processing_status, DocumentProcessingStatus.PARSED)
            self.assertNotIn(object_key, objects)
            self.assertEqual(client.deleted, [object_key])
            self.assertEqual(len(client.bodies), 1)
            self.assertTrue(client.bodies[0].closed)
            self.assertTrue(all(size == 1024 * 1024 for size in client.bodies[0].read_sizes))

    def test_s3_upload_confirmation_uses_get_when_head_is_forbidden(self) -> None:
        document_id = uuid.uuid4()
        object_key = build_object_key(self.workspace_id, document_id)
        content = build_text_pdf(2)
        client = FakeS3Client({object_key: content})
        client.head_error = FakeS3ForbiddenError("head forbidden")
        s3 = S3StorageProvider.__new__(S3StorageProvider)
        s3._client = client
        s3._bucket = "private-revia"
        s3._temp_root = Path(self.storage_directory.name)
        with self.Session() as db:
            db.add(Document(
                id=document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="head-forbidden.pdf",
                mime_type="application/pdf",
                size_bytes=len(content),
                storage_key=object_key,
                storage_backend="s3",
                processing_status=DocumentProcessingStatus.UPLOADED,
                processing_phase="uploaded",
            ))
            db.commit()
            service = DocumentProcessingService(
                db,
                s3,
                PDFParser(max_pages=600),
                TextStructurer(),
                StructuredTextSplitter(),
                max_upload_bytes=150 * 1024 * 1024,
            )
            confirmed = service.confirm_upload(self.workspace_id, self.project_id, document_id)
            self.assertEqual(confirmed.processing_status, DocumentProcessingStatus.QUEUED)
            self.assertEqual(confirmed.total_pages, 2)
        self.assertEqual(client.head_calls, 0)
        self.assertEqual(len(client.bodies), 1)
        self.assertTrue(client.bodies[0].closed)

    def test_s3_failed_resume_falls_back_to_get_when_head_is_forbidden(self) -> None:
        document_id = uuid.uuid4()
        object_key = build_object_key(self.workspace_id, document_id)
        content = build_text_pdf(3)
        client = FakeS3Client({object_key: content})
        client.head_error = FakeS3ForbiddenError("head forbidden")
        s3 = S3StorageProvider.__new__(S3StorageProvider)
        s3._client = client
        s3._bucket = "private-revia"
        s3._temp_root = Path(self.storage_directory.name)
        with self.Session() as db:
            db.add(Document(
                id=document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="resume-head-forbidden.pdf",
                mime_type="application/pdf",
                size_bytes=len(content),
                storage_key=object_key,
                storage_backend="s3",
                processing_status=DocumentProcessingStatus.FAILED,
                processing_phase="failed",
                total_pages=3,
                processed_pages=2,
                current_page=3,
                error_message="无法检查对象存储文件（403）",
            ))
            db.commit()
            service = DocumentProcessingService(
                db,
                s3,
                PDFParser(max_pages=600),
                TextStructurer(),
                StructuredTextSplitter(),
                max_upload_bytes=150 * 1024 * 1024,
            )
            queued = service.resume_failed_document(self.workspace_id, self.project_id, document_id)
            self.assertEqual(queued.processing_status, DocumentProcessingStatus.QUEUED)
            self.assertEqual(queued.current_page, 3)
        self.assertEqual(client.head_calls, 1)
        self.assertEqual(len(client.bodies), 1)
        self.assertTrue(client.bodies[0].closed)

    def test_s3_resume_uses_head_without_downloading_the_pdf(self) -> None:
        document_id = uuid.uuid4()
        object_key = build_object_key(self.workspace_id, document_id)
        content = build_text_pdf(3)
        client = FakeS3Client({object_key: content})
        s3 = S3StorageProvider.__new__(S3StorageProvider)
        s3._client = client
        s3._bucket = "private-revia"
        s3._temp_root = Path(self.storage_directory.name)
        with self.Session() as db:
            db.add(Document(
                id=document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="resume-head.pdf",
                mime_type="application/pdf",
                size_bytes=len(content),
                storage_key=object_key,
                storage_backend="s3",
                processing_status=DocumentProcessingStatus.FAILED,
                processing_phase="failed",
                total_pages=3,
                processed_pages=2,
                current_page=3,
                error_message="temporary storage failure",
            ))
            db.commit()
            service = DocumentProcessingService(
                db,
                s3,
                PDFParser(max_pages=600),
                TextStructurer(),
                StructuredTextSplitter(),
                max_upload_bytes=150 * 1024 * 1024,
            )
            queued = service.resume_failed_document(self.workspace_id, self.project_id, document_id)
            self.assertEqual(queued.processing_status, DocumentProcessingStatus.QUEUED)
        self.assertEqual(client.head_calls, 1)
        self.assertEqual(client.bodies, [])

    def test_s3_download_exposes_safe_error_code_without_object_path(self) -> None:
        object_key = build_object_key(self.workspace_id, uuid.uuid4())
        client = FakeS3Client({object_key: b"pdf"})
        client.get_error = FakeS3RequestError("secret endpoint detail")
        s3 = S3StorageProvider.__new__(S3StorageProvider)
        s3._client = client
        s3._bucket = "private-revia"
        s3._temp_root = Path(self.storage_directory.name)

        with self.assertRaisesRegex(StorageUnavailableError, "RequestTimeout") as captured:
            s3.download_to_temp(object_key)
        self.assertNotIn(object_key, str(captured.exception))


    def test_s3_download_logs_safe_provider_error_details(self) -> None:
        object_key = build_object_key(self.workspace_id, uuid.uuid4())
        client = FakeS3Client({object_key: b"pdf"})
        client.get_error = FakeDetailedS3AccessDeniedError("provider detail")
        s3 = S3StorageProvider.__new__(S3StorageProvider)
        s3._client = client
        s3._bucket = "private-revia"
        s3._temp_root = Path(self.storage_directory.name)

        with self.assertLogs("revia.storage", level="ERROR") as captured:
            with self.assertRaisesRegex(StorageUnavailableError, "AccessDenied"):
                s3.download_to_temp(object_key)

        logs = "\n".join(captured.output)
        self.assertIn("code=AccessDenied", logs)
        self.assertIn("message=application key is not authorized", logs)
        self.assertIn("status=403", logs)
        self.assertIn("request_id=request-123", logs)
        self.assertIn("host_id=host-456", logs)
        self.assertNotIn("workspaces/", logs)

    def test_s3_download_cap_has_clear_retryable_error(self) -> None:
        object_key = build_object_key(self.workspace_id, uuid.uuid4())
        client = FakeS3Client({object_key: b"pdf"})
        client.get_error = FakeS3DownloadCapError("cap exceeded")
        s3 = S3StorageProvider.__new__(S3StorageProvider)
        s3._client = client
        s3._bucket = "private-revia"
        s3._temp_root = Path(self.storage_directory.name)

        with self.assertLogs("revia.storage", level="ERROR"):
            with self.assertRaisesRegex(StorageDownloadLimitError, "今日下载额度已用完"):
                s3.download_to_temp(object_key)

    def test_upload_confirmation_storage_cap_marks_failed_and_preserves_pdf(self) -> None:
        document_id = uuid.uuid4()
        object_key = build_object_key(self.workspace_id, document_id)
        content = build_text_pdf(2)
        client = FakeS3Client({object_key: content})
        client.get_error = FakeS3DownloadCapError("cap exceeded")
        s3 = S3StorageProvider.__new__(S3StorageProvider)
        s3._client = client
        s3._bucket = "private-revia"
        s3._temp_root = Path(self.storage_directory.name)

        with self.Session() as db:
            db.add(Document(
                id=document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="cap-confirm.pdf",
                mime_type="application/pdf",
                size_bytes=len(content),
                storage_key=object_key,
                storage_backend="s3",
                processing_status=DocumentProcessingStatus.UPLOADED,
                processing_phase="uploading",
            ))
            db.commit()
            service = DocumentProcessingService(
                db,
                s3,
                PDFParser(max_pages=600),
                TextStructurer(),
                StructuredTextSplitter(),
                max_upload_bytes=150 * 1024 * 1024,
            )
            with self.assertLogs("revia.storage", level="ERROR"):
                with self.assertRaises(StorageDownloadLimitError):
                    service.confirm_upload(self.workspace_id, self.project_id, document_id)

            failed = db.get(Document, document_id)
            assert failed is not None
            self.assertEqual(failed.processing_status, DocumentProcessingStatus.FAILED)
            self.assertEqual(failed.processing_phase, "failed")
            self.assertIn("今日下载额度已用完", failed.error_message or "")
            self.assertEqual(failed.storage_key, object_key)
            self.assertIn(object_key, client.objects)
            self.assertEqual(client.deleted, [])
            project = db.get(Project, self.project_id)
            assert project is not None
            self.assertEqual(project.status, ProjectStatus.FAILED)

    def test_processing_storage_cap_fails_once_without_auto_retry_window(self) -> None:
        document_id = uuid.uuid4()
        object_key = build_object_key(self.workspace_id, document_id)
        content = build_text_pdf(2)
        client = FakeS3Client({object_key: content})
        client.get_error = FakeS3DownloadCapError("cap exceeded")
        s3 = S3StorageProvider.__new__(S3StorageProvider)
        s3._client = client
        s3._bucket = "private-revia"
        s3._temp_root = Path(self.storage_directory.name)

        with self.Session() as db:
            db.add(Document(
                id=document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="cap-processing.pdf",
                mime_type="application/pdf",
                size_bytes=len(content),
                storage_key=object_key,
                storage_backend="s3",
                processing_status=DocumentProcessingStatus.QUEUED,
                processing_phase="queued",
                total_pages=2,
                quota_pages=2,
            ))
            db.commit()
            service = DocumentProcessingService(
                db,
                s3,
                PDFParser(max_pages=600),
                TextStructurer(),
                StructuredTextSplitter(),
                max_upload_bytes=150 * 1024 * 1024,
            )
            with self.assertLogs("revia.storage", level="ERROR"):
                with self.assertRaisesRegex(DocumentProcessingError, "今日下载额度已用完"):
                    service.process_document(document_id)

            failed = db.get(Document, document_id)
            assert failed is not None
            self.assertEqual(failed.processing_status, DocumentProcessingStatus.FAILED)
            self.assertEqual(failed.processing_phase, "failed")
            self.assertIsNone(failed.retry_not_before)
            self.assertIn("今日下载额度已用完", failed.error_message or "")
            self.assertEqual(failed.storage_key, object_key)

    def test_failed_document_can_requeue_and_resume_from_saved_pages(self) -> None:
        document_id = self._document(3)
        with self.Session() as db:
            document = db.get(Document, document_id)
            assert document is not None
            document.processing_status = DocumentProcessingStatus.FAILED
            document.processing_phase = "failed"
            document.total_pages = 3
            document.processed_pages = 2
            document.current_page = 3
            document.retry_count = 50
            document.error_message = "无法从对象存储下载 PDF"
            for page_number in (1, 2):
                db.add(DocumentPage(
                    document_id=document_id,
                    page_number=page_number,
                    status=DocumentPageStatus.COMPLETED,
                    extraction_method=ExtractionMethod.TEXT,
                    extracted_text=f"saved page {page_number}",
                    character_count=12,
                ))
            db.commit()

            service = self._service(db)
            queued = service.resume_failed_document(self.workspace_id, self.project_id, document_id)
            self.assertEqual(queued.processing_status, DocumentProcessingStatus.QUEUED)
            self.assertEqual(queued.processing_phase, "queued")
            self.assertEqual(queued.current_page, 3)
            self.assertEqual(queued.retry_count, 0)
            self.assertIsNone(queued.error_message)
            project = db.get(Project, self.project_id)
            assert project is not None
            self.assertEqual(project.status, ProjectStatus.PROCESSING)

            completed = service.process_document(document_id)
            self.assertEqual(completed.processing_status, DocumentProcessingStatus.PARSED)
            pages = list(db.scalars(
                select(DocumentPage)
                .where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_number)
            ).all())
            self.assertEqual([page.page_number for page in pages], [1, 2, 3])
            self.assertEqual(pages[0].extracted_text, "saved page 1")
            self.assertEqual(pages[1].extracted_text, "saved page 2")

    def test_missing_resume_object_marks_document_failed_instead_of_stuck_processing(self) -> None:
        document_id = uuid.uuid4()
        object_key = build_object_key(self.workspace_id, document_id)
        with self.Session() as db:
            db.add(Document(
                id=document_id,
                project_id=self.project_id,
                kind=DocumentKind.COURSE_MATERIAL,
                original_name="missing.pdf",
                mime_type="application/pdf",
                size_bytes=10,
                storage_key=object_key,
                storage_backend="local",
                processing_status=DocumentProcessingStatus.INTERRUPTED,
                processing_phase="interrupted",
            ))
            db.commit()
            with self.assertRaisesRegex(DocumentProcessingError, "已不存在"):
                self._service(db).process_document(document_id)
            failed = db.get(Document, document_id)
            assert failed is not None
            self.assertEqual(failed.processing_status, DocumentProcessingStatus.FAILED)
            self.assertEqual(failed.processing_phase, "failed")


if __name__ == "__main__":
    unittest.main()
