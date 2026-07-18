import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedSyllabusSection:
    chapter: str | None
    items: list[str]


@dataclass(frozen=True)
class ParsedSyllabusItem:
    chapter: str | None
    title: str
    parent_title: str | None = None


@dataclass(frozen=True)
class _LineItem:
    title: str
    indent: int
    numbered: bool
    number_depth: int


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
    _collection_pattern = re.compile(
        r"(?:特征|特点|类型|原则|步骤|流程|因素|模块|构成|内容|方法)(?:是|为|包括|包含|如下|[：:]?\s*)$"
        r"|^(?:各|经典|主要|核心).*(?:考点|理论|方法|模型)$"
    )

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

    def flatten_hierarchy(self, text: str) -> list[ParsedSyllabusItem]:
        """Preserve explicit parent-child hints without changing the stored syllabus text."""
        entries: list[ParsedSyllabusItem] = []
        current_chapter: str | None = None
        current_lines: list[_LineItem] = []
        seen: set[str] = set()

        def flush() -> None:
            nonlocal current_lines
            active_parent: _LineItem | None = None
            for line in current_lines:
                parent_title: str | None = None
                if active_parent is not None:
                    explicitly_nested = line.indent > active_parent.indent
                    nested_numbering = (
                        active_parent.numbered
                        and line.numbered
                        and line.number_depth > active_parent.number_depth
                    )
                    numbered_children = not active_parent.numbered and line.numbered
                    if explicitly_nested or nested_numbering or numbered_children:
                        parent_title = active_parent.title
                    else:
                        active_parent = None
                entries.append(ParsedSyllabusItem(
                    chapter=current_chapter,
                    title=line.title,
                    parent_title=parent_title,
                ))
                if self._is_collection_title(line.title):
                    active_parent = line
            current_lines = []

        for raw_line in text.splitlines():
            stripped = raw_line.lstrip(" \t")
            indent = len(raw_line) - len(stripped) + raw_line[: len(raw_line) - len(stripped)].count("\t") * 3
            line = self._clean(stripped)
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
            number_token = line[: numbered.start(1)] if numbered else ""
            number_depth = max(1, len(re.findall(r"\d+", number_token))) if numbered else 0
            current_lines.append(_LineItem(
                title=item,
                indent=indent,
                numbered=numbered is not None,
                number_depth=number_depth,
            ))
        flush()
        return entries

    def _clean(self, value: str) -> str:
        value = value.strip().strip("•·●○▪▫◆◇—–-_=~*#|/\\")
        value = re.sub(r"\s+", " ", value).strip()
        if not value or self._meaningless_pattern.fullmatch(value):
            return ""
        return value

    @staticmethod
    def _dedupe_key(value: str) -> str:
        return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()

    @classmethod
    def _is_collection_title(cls, value: str) -> bool:
        return bool(cls._collection_pattern.search(value.strip()))
