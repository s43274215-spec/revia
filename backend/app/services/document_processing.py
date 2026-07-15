import uuid

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.document.parser import PDFParser, PDFParsingError
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.models.document import ParsedDocument, ParsedPage, TextChunk
from app.models.enums import DocumentKind, DocumentProcessingStatus
from app.models.project import Document, Project
from app.services.storage import LocalFileStorage, UploadLimitError


class DocumentProcessingError(RuntimeError):
    pass


class DocumentProjectNotFoundError(LookupError):
    pass


class DocumentProcessingService:
    def __init__(
        self,
        db: Session,
        storage: LocalFileStorage,
        parser: PDFParser,
        structurer: TextStructurer,
        splitter: StructuredTextSplitter,
    ) -> None:
        self._db = db
        self._storage = storage
        self._parser = parser
        self._structurer = structurer
        self._splitter = splitter

    async def process_upload(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        kind: DocumentKind,
        file: UploadFile,
    ) -> Document:
        project = self._db.query(Project).filter_by(id=project_id, workspace_id=workspace_id).one_or_none()
        if project is None:
            raise DocumentProjectNotFoundError(f"Project {project_id} was not found")
        if file.content_type != "application/pdf" and not (file.filename or "").lower().endswith(".pdf"):
            raise DocumentProcessingError("Only PDF files are supported")

        document_id = uuid.uuid4()
        storage_key: str | None = None
        document: Document | None = None
        try:
            storage_key, size_bytes = await self._storage.save_pdf(project_id, document_id, file)
            document = Document(
                id=document_id,
                project_id=project_id,
                kind=kind,
                original_name=file.filename or f"{document_id}.pdf",
                mime_type="application/pdf",
                size_bytes=size_bytes,
                storage_key=storage_key,
                processing_status=DocumentProcessingStatus.UPLOADED,
            )
            self._db.add(document)
            self._db.commit()
            self._db.refresh(document)

            document.processing_status = DocumentProcessingStatus.PARSING
            self._db.commit()

            parsed_pdf = self._parser.parse(self._storage.resolve(storage_key))
            structured = self._structurer.structure(parsed_pdf)
            chunks = self._splitter.split(structured)
            parsed_document = ParsedDocument(
                document_id=document.id,
                page_count=parsed_pdf.page_count,
                raw_text=parsed_pdf.raw_text,
                parser_name=parsed_pdf.parser_name,
                parser_version=parsed_pdf.parser_version,
                is_scanned=parsed_pdf.is_scanned,
                ocr_executed=parsed_pdf.ocr_executed,
                ocr_page_count=parsed_pdf.ocr_page_count,
                ocr_error=parsed_pdf.ocr_error,
                pages=[ParsedPage(page_number=page.page_number, text=page.text) for page in parsed_pdf.pages],
                chunks=[
                    TextChunk(
                        position=chunk.position,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        chapter_title=chunk.chapter_title,
                        section_title=chunk.section_title,
                        content=chunk.content,
                    )
                    for chunk in chunks
                ],
            )
            document.parsed_document = parsed_document
            document.processing_status = DocumentProcessingStatus.PARSED
            self._db.commit()
            self._db.refresh(document)
            self._storage.delete(storage_key)
            document.storage_key = None
            self._db.commit()
            return document
        except (DocumentProjectNotFoundError, DocumentProcessingError):
            raise
        except Exception as exc:
            self._db.rollback()
            if document:
                failed_document = self._db.get(Document, document.id)
                if failed_document:
                    failed_document.processing_status = DocumentProcessingStatus.FAILED
                    failed_document.error_message = str(exc)
                    failed_document.storage_key = None
                    self._db.commit()
            if isinstance(exc, UploadLimitError):
                raise DocumentProcessingError(str(exc)) from exc
            if isinstance(exc, PDFParsingError):
                raise DocumentProcessingError(str(exc)) from exc
            raise DocumentProcessingError("PDF processing failed") from exc
        finally:
            if storage_key:
                try:
                    self._storage.delete(storage_key)
                except OSError:
                    pass
