import unittest
import uuid
import tempfile
from types import SimpleNamespace
from pathlib import Path

import fitz

from app.document.parser import PDFParser, ParsedPDF, ParsedPageData, SourceOutlineEntry
from app.document.splitter import StructuredTextSplitter
from app.document.structure import TextStructurer
from app.services.generation import GenerationWorkflowService
from app.services.content_organization import INTERNAL_UNRESOLVED_CHAPTER


def parsed_pdf(text: str, outline=()) -> ParsedPDF:
    return ParsedPDF(
        page_count=1,
        pages=[ParsedPageData(1, text)],
        parser_name="test",
        parser_version="test",
        is_scanned=False,
        ocr_executed=False,
        ocr_page_count=0,
        ocr_error=None,
        outline=tuple(outline),
    )


def record(candidates, source_ids, syllabus_chapter="题纲父标题"):
    bullet = SimpleNamespace(source_chunk_ids=source_ids)
    result = SimpleNamespace(bullet_points=[bullet])
    return SimpleNamespace(
        syllabus_chapter=syllabus_chapter,
        result=result,
        candidates=candidates,
    )


def candidate(chapter, page, score=0.8):
    return SimpleNamespace(
        chunk_id=uuid.uuid4(),
        chapter=chapter,
        page_start=page,
        page_end=page,
        score=score,
        text=f"{chapter}\n这是用于确认章节标题之后确实存在足够具体主题正文的测试内容。" if chapter else "正文内容",
    )


class SourceChapterTests(unittest.TestCase):
    def test_parser_reads_real_pdf_bookmarks(self) -> None:
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "ordinary content")
        document.set_toc([[1, "PDF 目录章节", 1]])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outlined.pdf"
            document.save(path)
            document.close()
            parsed = PDFParser().parse(path)
        self.assertEqual(parsed.outline[0].title, "PDF 目录章节")
        self.assertEqual(parsed.outline[0].page_number, 1)

    def test_pdf_outline_has_priority_for_new_chunks(self) -> None:
        parsed = parsed_pdf(
            "普通正文，不含可识别的章标题。",
            [SourceOutlineEntry(level=1, title="书签章节：市场结构", page_number=1)],
        )
        chunks = StructuredTextSplitter().split(TextStructurer().structure(parsed))
        self.assertEqual(chunks[0].chapter_title, "书签章节：市场结构")

    def test_detected_heading_is_used_without_pdf_outline(self) -> None:
        chunks = StructuredTextSplitter().split(TextStructurer().structure(parsed_pdf(
            "第一章 人力资源管理\n人力资本具有增值性。",
        )))
        self.assertEqual(chunks[0].chapter_title, "第一章 人力资源管理")

    def test_limited_scan_title_information_remains_unclassified_at_chunk_stage(self) -> None:
        chunks = StructuredTextSplitter().split(TextStructurer().structure(parsed_pdf(
            "扫描识别正文，只有连续内容，没有章节标题。",
        )))
        self.assertIsNone(chunks[0].chapter_title)

    def test_primary_evidence_chapter_wins_without_duplicating_cross_chapter_point(self) -> None:
        primary = candidate("资料第一章", 12, 0.91)
        secondary = candidate("资料第二章", 30, 0.95)
        value = record(
            [primary, secondary],
            [primary.chunk_id, primary.chunk_id, secondary.chunk_id],
        )
        self.assertEqual(GenerationWorkflowService._source_chapter(value), "资料第一章")

    def test_unresolved_chapters_use_an_internal_hidden_container(self) -> None:
        unknown = candidate(None, 42)
        value = record([unknown], [unknown.chunk_id], syllabus_chapter="不能作为资料章节")
        self.assertEqual(GenerationWorkflowService._source_chapter(value), INTERNAL_UNRESOLVED_CHAPTER)
        empty = record([], [], syllabus_chapter="也不能使用")
        self.assertEqual(GenerationWorkflowService._source_chapter(empty), INTERNAL_UNRESOLVED_CHAPTER)

    def test_source_chapter_is_distinct_from_syllabus_parent_title(self) -> None:
        source = candidate("PDF 自身章节", 8)
        value = record([source], [source.chunk_id], syllabus_chapter="题纲集合项")
        self.assertEqual(GenerationWorkflowService._source_chapter(value), "PDF 自身章节")
        self.assertNotEqual(GenerationWorkflowService._source_chapter(value), value.syllabus_chapter)


if __name__ == "__main__":
    unittest.main()
