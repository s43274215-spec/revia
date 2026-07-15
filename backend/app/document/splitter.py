import re
from dataclasses import dataclass

from app.document.structure import BlockKind, StructuredText, TextBlock


@dataclass(frozen=True)
class TextChunkData:
    position: int
    page_start: int
    page_end: int
    chapter_title: str | None
    section_title: str | None
    content: str


class StructuredTextSplitter:
    def __init__(self, target_size: int = 1800, maximum_size: int = 2600) -> None:
        if target_size <= 0 or maximum_size < target_size:
            raise ValueError("Invalid text splitter sizes")
        self.target_size = target_size
        self.maximum_size = maximum_size

    def split(self, structured: StructuredText) -> list[TextChunkData]:
        chunks: list[TextChunkData] = []
        pending: list[TextBlock] = []
        chapter_title: str | None = None
        section_title: str | None = None

        def flush() -> None:
            if not pending:
                return
            content = "\n\n".join(block.raw_text for block in pending).strip()
            if content:
                chunks.append(TextChunkData(
                    position=len(chunks),
                    page_start=min(block.page_number for block in pending),
                    page_end=max(block.page_number for block in pending),
                    chapter_title=chapter_title,
                    section_title=section_title,
                    content=content,
                ))
            pending.clear()

        for block in structured.blocks:
            if block.kind == BlockKind.CHAPTER:
                flush()
                chapter_title = block.raw_text
                section_title = None
                pending.append(block)
                continue
            if block.kind == BlockKind.SECTION:
                flush()
                section_title = block.raw_text
                pending.append(block)
                continue

            for part in self._split_oversized_block(block):
                current_size = sum(len(item.raw_text) for item in pending)
                if pending and current_size + len(part.raw_text) > self.target_size:
                    flush()
                pending.append(part)
        flush()
        return chunks

    def _split_oversized_block(self, block: TextBlock) -> list[TextBlock]:
        if len(block.raw_text) <= self.maximum_size:
            return [block]
        sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])\s*", block.raw_text) if item.strip()]
        if len(sentences) <= 1:
            return self._fallback_split(block)
        parts: list[TextBlock] = []
        buffer = ""
        for sentence in sentences:
            if buffer and len(buffer) + len(sentence) > self.target_size:
                parts.append(TextBlock(block.kind, block.page_number, buffer))
                buffer = ""
            if len(sentence) > self.maximum_size:
                if buffer:
                    parts.append(TextBlock(block.kind, block.page_number, buffer))
                    buffer = ""
                parts.extend(self._fallback_split(TextBlock(block.kind, block.page_number, sentence)))
            else:
                buffer += sentence
        if buffer:
            parts.append(TextBlock(block.kind, block.page_number, buffer))
        return parts

    def _fallback_split(self, block: TextBlock) -> list[TextBlock]:
        words = block.raw_text.split()
        if len(words) > 1:
            parts: list[TextBlock] = []
            buffer: list[str] = []
            for word in words:
                if buffer and len(" ".join(buffer + [word])) > self.target_size:
                    parts.append(TextBlock(block.kind, block.page_number, " ".join(buffer)))
                    buffer = []
                buffer.append(word)
            if buffer:
                parts.append(TextBlock(block.kind, block.page_number, " ".join(buffer)))
            return parts
        return [
            TextBlock(block.kind, block.page_number, block.raw_text[start:start + self.maximum_size])
            for start in range(0, len(block.raw_text), self.maximum_size)
        ]
