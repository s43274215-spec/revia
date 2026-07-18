import re
from dataclasses import dataclass

from app.matching.aliases import normalize_query_key


@dataclass(frozen=True)
class PreparedMatchingQuery:
    syllabus_item_original: str
    matching_query: str
    core_phrase: str
    expanded_keywords: tuple[str, ...]


_EXAM_INSTRUCTION_MARKERS = (
    "需掌握",
    "重点掌握",
    "核心考点",
    "了解",
    "理解为主",
    "无需死记硬背",
    "需在",
    "需区分",
    "不能将",
    "考试说明",
    "作答要求",
    "背诵要求",
)

_CONTROLLED_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "人口人力人才的关系": ("人口资源", "人力资源", "人才资源"),
    "人口人力人才关系": ("人口资源", "人力资源", "人才资源"),
    "人力资源的五大特征": ("能动性", "可再生性", "增值性", "时效性", "社会性"),
    "人力资源管理的核心内容": (
        "人力资源规划",
        "招聘",
        "培训",
        "绩效",
        "薪酬",
        "员工关系",
    ),
    "人力资本": ("舒尔茨", "贝克尔", "知识技能", "能力资本"),
}


class MatchingQueryPreprocessor:
    def prepare(self, syllabus_item: str) -> PreparedMatchingQuery:
        original = syllabus_item.strip()
        core_phrase, separator, tail = self._split_topic(original)
        retained_tail = self._retain_knowledge_clauses(tail) if separator else ""
        matching_query = core_phrase
        if retained_tail:
            matching_query = f"{core_phrase}：{retained_tail}"
        matching_query = matching_query.strip(" ，,；;。：:") or original
        expansion_key = self._normalize(core_phrase)
        return PreparedMatchingQuery(
            syllabus_item_original=original,
            matching_query=matching_query,
            core_phrase=core_phrase.strip() or matching_query,
            expanded_keywords=_CONTROLLED_EXPANSIONS.get(expansion_key, ()),
        )

    @staticmethod
    def _split_topic(value: str) -> tuple[str, str, str]:
        match = re.search(r"[：:]", value)
        if match is None:
            return value, "", ""
        return value[: match.start()], match.group(), value[match.end() :]

    @staticmethod
    def _retain_knowledge_clauses(value: str) -> str:
        retained: list[str] = []
        for clause in re.split(r"[，,；;。]+", value):
            cleaned = clause.strip()
            if not cleaned:
                continue
            if any(marker in cleaned for marker in _EXAM_INSTRUCTION_MARKERS):
                break
            retained.append(cleaned)
        return "、".join(retained)

    @staticmethod
    def _normalize(value: str) -> str:
        return normalize_query_key(value)
