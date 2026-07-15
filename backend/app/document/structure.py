import re
from dataclasses import dataclass
from enum import StrEnum

from app.document.parser import ParsedPDF


class BlockKind(StrEnum):
    CHAPTER = "chapter"
    SECTION = "section"
    CONTENT = "content"


@dataclass(frozen=True)
class TextBlock:
    kind: BlockKind
    page_number: int
    raw_text: str


@dataclass(frozen=True)
class StructuredText:
    blocks: list[TextBlock]


class TextStructurer:
    _chapter_pattern = re.compile(r"^\s*第[一二三四五六七八九十百零〇0-9]+章(?:\s+|[：:、.-])?.+\s*$")
    _section_pattern = re.compile(
        r"^\s*(?:第[一二三四五六七八九十百零〇0-9]+节|\d+(?:\.\d+)+)(?:\s+|[：:、.-])?.+\s*$"
    )

    def structure(self, parsed: ParsedPDF) -> StructuredText:
        blocks: list[TextBlock] = []
        for page in parsed.pages:
            content_lines: list[str] = []
            for line in page.text.splitlines():
                stripped = line.strip()
                if not stripped:
                    self._flush_content(blocks, page.page_number, content_lines)
                    continue
                heading_kind = self._heading_kind(stripped)
                if heading_kind:
                    self._flush_content(blocks, page.page_number, content_lines)
                    blocks.append(TextBlock(kind=heading_kind, page_number=page.page_number, raw_text=stripped))
                else:
                    content_lines.append(stripped)
            self._flush_content(blocks, page.page_number, content_lines)
        return StructuredText(blocks=blocks)

    def _heading_kind(self, text: str) -> BlockKind | None:
        if self._chapter_pattern.fullmatch(text):
            return BlockKind.CHAPTER
        if self._section_pattern.fullmatch(text):
            return BlockKind.SECTION
        return None

    @staticmethod
    def _flush_content(blocks: list[TextBlock], page_number: int, lines: list[str]) -> None:
        if lines:
            blocks.append(TextBlock(kind=BlockKind.CONTENT, page_number=page_number, raw_text="\n".join(lines)))
            lines.clear()
