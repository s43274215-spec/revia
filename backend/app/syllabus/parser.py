import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedSyllabusSection:
    chapter: str | None
    items: list[str]


class SyllabusParser:
    _chapter_pattern = re.compile(r"^第\s*[一二三四五六七八九十百千万零〇两\d]+\s*章(?:\s+|[：:、.-]*)(.+)?$")
    _numbered_pattern = re.compile(
        r"^(?:"
        r"\d+(?:\.\d+)*(?:[.、．:：)）]\s*|\s+)"
        r"|[（(]\s*(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+)\s*[）)]\s*"
        r"|[一二三四五六七八九十]+[.、．:：]\s*"
        r")(.+)$"
    )
    _meaningless_pattern = re.compile(r"^[\W_]+$", re.UNICODE)

    def parse(self, text: str) -> list[ParsedSyllabusSection]:
        sections: list[ParsedSyllabusSection] = []
        current_chapter: str | None = None
        current_items: list[str] = []
        seen: set[str] = set()

        def flush() -> None:
            nonlocal current_items
            if current_items:
                sections.append(ParsedSyllabusSection(chapter=current_chapter, items=current_items))
                current_items = []

        for raw_line in text.splitlines():
            line = self._clean(raw_line)
            if not line:
                continue
            if self._chapter_pattern.match(line):
                flush()
                current_chapter = line
                continue
            numbered = self._numbered_pattern.match(line)
            item = self._clean(numbered.group(1) if numbered else line)
            normalized = self._dedupe_key(item)
            if not item or normalized in seen:
                continue
            seen.add(normalized)
            current_items.append(item)
        flush()
        return sections

    def flatten(self, text: str) -> list[tuple[str | None, str]]:
        return [(section.chapter, item) for section in self.parse(text) for item in section.items]

    def _clean(self, value: str) -> str:
        value = value.strip().strip("•·●○▪▫◆◇—–-_=~*#|/\\")
        value = re.sub(r"\s+", " ", value).strip()
        if not value or self._meaningless_pattern.fullmatch(value):
            return ""
        return value

    @staticmethod
    def _dedupe_key(value: str) -> str:
        return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()
