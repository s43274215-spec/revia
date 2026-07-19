from __future__ import annotations

import io
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from docx import Document as WordDocument
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.content import BulletPoint, Chapter, KnowledgePoint
from app.models.enums import ContentVersionKind
from app.models.project import GenerationJob, Project


VERSION_LABELS = {
    ContentVersionKind.KEYWORDS: "简洁版",
    ContentVersionKind.RECITATION: "标准版",
    ContentVersionKind.ORIGINAL: "详细版",
}
ALL_VERSION_ORDER = (
    ContentVersionKind.KEYWORDS,
    ContentVersionKind.RECITATION,
    ContentVersionKind.ORIGINAL,
)
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*：／＼\x00-\x1f]+')
_ORDERED_ITEM = re.compile(r"^\s*(?:\d+[.、)）]|[（(][一二三四五六七八九十百\d]+[）)])\s*(.+)$")
_BULLET_ITEM = re.compile(r"^\s*[-–—•·]\s*(.+)$")
_SECRET = re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{8,}|(?:password|secret|token|api[_ -]?key)\s*[:=]\s*\S+)")


class WordExportNotFoundError(LookupError):
    pass


def safe_export_filename(project_name: str, version_label: str, exported_at: datetime) -> str:
    cleaned = _UNSAFE_FILENAME.sub("-", project_name).strip(" .-") or "Revia学习材料"
    cleaned = re.sub(r"\s+", " ", cleaned)[:80].rstrip(" .-")
    return f"{cleaned}-{version_label}-{exported_at:%Y-%m-%d}.docx"


def content_disposition(filename: str) -> str:
    ascii_fallback = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip("-") or "revia-export.docx"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


class WordExportService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def export(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        selected: ContentVersionKind | None,
        exported_at: datetime | None = None,
    ) -> tuple[io.BytesIO, str]:
        project = self._db.scalar(
            select(Project)
            .where(Project.id == project_id, Project.workspace_id == workspace_id)
            .options(
                selectinload(Project.chapters)
                .selectinload(Chapter.knowledge_points)
                .selectinload(KnowledgePoint.bullet_points)
                .selectinload(BulletPoint.versions),
                selectinload(Project.chapters)
                .selectinload(Chapter.knowledge_points)
                .selectinload(KnowledgePoint.bullet_points)
                .selectinload(BulletPoint.sources),
            )
        )
        if project is None:
            raise WordExportNotFoundError("项目不存在")
        now = exported_at or datetime.now(UTC)
        document = WordDocument()
        self._configure_styles(document)
        document.core_properties.title = project.name
        document.core_properties.subject = "Revia 学习材料"
        document.add_heading(project.name, level=0)
        document.add_paragraph(f"导出时间：{now.astimezone().strftime('%Y-%m-%d %H:%M')}")
        version_label = "全部版本" if selected is None else VERSION_LABELS[selected]
        document.add_paragraph(f"导出范围：{version_label}")

        for chapter in sorted(project.chapters, key=lambda item: item.position):
            document.add_heading(chapter.title, level=1)
            for knowledge_point in sorted(chapter.knowledge_points, key=lambda item: item.position):
                document.add_heading(knowledge_point.title, level=2)
                bullets = sorted(knowledge_point.bullet_points, key=lambda item: item.position)
                if selected is None:
                    for kind in ALL_VERSION_ORDER:
                        document.add_heading(VERSION_LABELS[kind], level=3)
                        for bullet in bullets:
                            self._add_bullet_version(document, bullet, kind, heading_level=4)
                else:
                    for bullet in bullets:
                        self._add_bullet_version(document, bullet, selected, heading_level=3)

        failures = self._latest_failures(project.id)
        if failures:
            document.add_page_break()
            document.add_heading("未生成条目附录", level=1)
            for failure in failures:
                title = str(failure.get("syllabus_item") or "未命名条目")[:160]
                reason = _SECRET.sub("[敏感信息已隐藏]", str(failure.get("reason") or "未提供失败原因"))[:500]
                paragraph = document.add_paragraph(style="List Bullet")
                paragraph.add_run(title).bold = True
                paragraph.add_run(f"：{reason}")

        payload = io.BytesIO()
        document.save(payload)
        payload.seek(0)
        return payload, safe_export_filename(project.name, version_label, now)

    def _latest_failures(self, project_id: uuid.UUID) -> list[dict[str, object]]:
        job = self._db.scalar(
            select(GenerationJob)
            .where(GenerationJob.project_id == project_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
        return list(job.item_failures or []) if job is not None else []

    @staticmethod
    def _configure_styles(document: WordDocument) -> None:
        for style_name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "Heading 4"):
            style = document.styles[style_name]
            style.font.name = "Microsoft YaHei"
            style._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        if "Revia Source" not in document.styles:
            source = document.styles.add_style("Revia Source", WD_STYLE_TYPE.PARAGRAPH)
            source.font.name = "Microsoft YaHei"
            source._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
            source.font.size = document.styles["Normal"].font.size

    def _add_bullet_version(
        self,
        document: WordDocument,
        bullet: BulletPoint,
        kind: ContentVersionKind,
        *,
        heading_level: int,
    ) -> None:
        version = next((item for item in bullet.versions if item.kind == kind), None)
        if version is None:
            return
        document.add_heading(version.title, level=heading_level)
        self._add_content(document, version.content)
        if bullet.sources:
            pages = sorted({
                page
                for source in bullet.sources
                for page in range(source.page_start, source.page_end + 1)
            })
            page_text = "、".join(str(page) for page in pages)
            document.add_paragraph(f"来源：第 {page_text} 页", style="Revia Source")

    @staticmethod
    def _add_content(document: WordDocument, content: str) -> None:
        for raw_block in re.split(r"\n\s*\n", content.strip()):
            lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
            if not lines:
                continue
            for line in lines:
                ordered = _ORDERED_ITEM.match(line)
                bullet = _BULLET_ITEM.match(line)
                if ordered:
                    document.add_paragraph(ordered.group(1), style="List Number")
                elif bullet:
                    document.add_paragraph(bullet.group(1), style="List Bullet")
                else:
                    document.add_paragraph(line)
