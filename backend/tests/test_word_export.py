import unittest
import uuid
import zipfile
from datetime import UTC, datetime

from docx import Document as WordDocument
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.base import Base
from app.models.content import BulletPoint, BulletPointSource, Chapter, ContentVersion, KnowledgePoint
from app.models.enums import ContentVersionKind, GenerationStatus
from app.models.project import GenerationJob, Project
from app.models.workspace import Workspace
from app.services.word_export import WordExportNotFoundError, WordExportService


class WordExportTests(unittest.TestCase):
    def setUp(self) -> None:
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
            project = Project(id=self.project_id, workspace_id=self.workspace_id, name="财政/政策：复习")
            chapter = Chapter(title="资料章节：财政政策", position=0)
            knowledge = KnowledgePoint(title="财政政策工具", position=0)
            bullet = BulletPoint(position=0)
            bullet.versions = [
                ContentVersion(kind=ContentVersionKind.ORIGINAL, title="政策作用机制", content="原文版本正文。\n\n1. 政府支出\n2. 税收"),
                ContentVersion(kind=ContentVersionKind.RECITATION, title="政策作用机制", content="背诵版本考试表达。"),
                ContentVersion(kind=ContentVersionKind.KEYWORDS, title="政策作用机制", content="- 支出\n- 税收"),
            ]
            bullet.sources = [BulletPointSource(
                text_chunk_id=uuid.uuid4(),
                page_start=12,
                page_end=13,
            )]
            knowledge.bullet_points.append(bullet)
            chapter.knowledge_points.append(knowledge)
            project.chapters.append(chapter)
            db.add(project)
            db.add(GenerationJob(
                project_id=self.project_id,
                status=GenerationStatus.PARTIAL_FAILED,
                provider="mock",
                item_failures=[{
                    "syllabus_item": "自动稳定器",
                    "reason": "token=secret-value 未找到足够资料依据",
                }],
            ))
            db.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _text(payload) -> tuple[WordDocument, str]:
        payload.seek(0)
        document = WordDocument(payload)
        return document, "\n".join(paragraph.text for paragraph in document.paragraphs)

    def test_current_version_exports_one_version_with_hierarchy_lists_sources_and_appendix(self) -> None:
        with self.Session() as db:
            payload, filename = WordExportService(db).export(
                self.workspace_id,
                self.project_id,
                ContentVersionKind.ORIGINAL,
                datetime(2026, 7, 19, tzinfo=UTC),
            )
        self.assertTrue(zipfile.is_zipfile(payload))
        document, text = self._text(payload)
        self.assertIn("财政/政策：复习", text)
        self.assertIn("资料章节：财政政策", text)
        self.assertIn("财政政策工具", text)
        self.assertIn("政策作用机制", text)
        self.assertIn("原文版本正文", text)
        self.assertNotIn("背诵版本考试表达", text)
        self.assertNotIn("关键词版本", text)
        self.assertIn("来源：第 12、13 页", text)
        self.assertIn("未生成条目附录", text)
        self.assertIn("自动稳定器", text)
        self.assertNotIn("secret-value", text)
        self.assertTrue(any(paragraph.style.name == "List Number" for paragraph in document.paragraphs))
        self.assertNotIn("/", filename)
        self.assertNotIn("：", filename)
        self.assertTrue(filename.endswith("-原文版本-2026-07-19.docx"))

    def test_all_versions_contains_three_labels_and_valid_docx_parts(self) -> None:
        with self.Session() as db:
            payload, filename = WordExportService(db).export(self.workspace_id, self.project_id, None)
        with zipfile.ZipFile(payload) as archive:
            self.assertIn("[Content_Types].xml", archive.namelist())
            self.assertIn("word/document.xml", archive.namelist())
        _, text = self._text(payload)
        for label in ("原文版本", "背诵版本", "关键词版本"):
            self.assertIn(label, text)
        self.assertLess(text.index("原文版本"), text.index("背诵版本"))
        self.assertLess(text.index("背诵版本"), text.index("关键词版本"))
        self.assertIn("背诵版本考试表达", text)
        self.assertIn("全部版本", filename)

    def test_export_is_workspace_scoped(self) -> None:
        with self.Session() as db:
            with self.assertRaises(WordExportNotFoundError):
                WordExportService(db).export(uuid.uuid4(), self.project_id, None)


if __name__ == "__main__":
    unittest.main()
