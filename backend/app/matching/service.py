import re
from difflib import SequenceMatcher

from app.matching.preprocessing import MatchingQueryPreprocessor, PreparedMatchingQuery
from app.matching.schemas import CandidateChunk, ItemMatch
from app.models.document import TextChunk


class MatchingService:
    def __init__(
        self,
        *,
        threshold: float,
        max_candidates: int,
        fallback_threshold: float = 0.28,
        preprocessor: MatchingQueryPreprocessor | None = None,
    ) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("Matching threshold must be between 0 and 1")
        if not 0 <= fallback_threshold <= threshold:
            raise ValueError("Matching fallback threshold must be between zero and the primary threshold")
        if max_candidates < 1:
            raise ValueError("Matching max_candidates must be positive")
        self._threshold = threshold
        self._fallback_threshold = fallback_threshold
        self._max_candidates = max_candidates
        self._preprocessor = preprocessor or MatchingQueryPreprocessor()

    def match_item(
        self,
        *,
        syllabus_item: str,
        syllabus_chapter: str | None,
        chunks: list[TextChunk],
    ) -> ItemMatch:
        prepared = self._preprocessor.prepare(syllabus_item)
        scored: list[tuple[float, TextChunk]] = []
        for chunk in chunks:
            score = self._score(prepared.matching_query, syllabus_chapter, chunk)
            if self._requires_strict_short_evidence(prepared) and not self._has_recall_evidence(prepared, chunk):
                continue
            scored.append((score, chunk))

        primary = self._build_candidates(
            prepared=prepared,
            scored=scored,
            threshold=self._threshold,
            require_recall_evidence=False,
        )
        recall_stage = "primary" if primary else None
        candidates = primary
        if not candidates:
            candidates = self._build_candidates(
                prepared=prepared,
                scored=scored,
                threshold=self._fallback_threshold,
                require_recall_evidence=True,
            )
            recall_stage = "secondary" if candidates else None

        candidates.sort(key=lambda candidate: (-candidate.score, candidate.page_start, str(candidate.chunk_id)))
        limited = candidates[: self._max_candidates]
        return ItemMatch(
            syllabus_item=prepared.syllabus_item_original,
            syllabus_item_original=prepared.syllabus_item_original,
            matching_query=prepared.matching_query,
            chapter=syllabus_chapter,
            matched=bool(limited),
            candidates=limited,
            recall_stage=recall_stage,
            reason=None if limited else "unmatched: no TextChunk met the configured relevance threshold",
        )

    def _build_candidates(
        self,
        *,
        prepared: PreparedMatchingQuery,
        scored: list[tuple[float, TextChunk]],
        threshold: float,
        require_recall_evidence: bool,
    ) -> list[CandidateChunk]:
        candidates: list[CandidateChunk] = []
        for score, chunk in scored:
            if score < threshold:
                continue
            if require_recall_evidence and not self._has_recall_evidence(prepared, chunk):
                continue
            candidates.append(CandidateChunk(
                syllabus_item=prepared.syllabus_item_original,
                chunk_id=chunk.id,
                score=round(score, 4),
                chapter=chunk.chapter_title,
                section=chunk.section_title,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                text=chunk.content,
            ))
        return candidates

    def _has_recall_evidence(self, prepared: PreparedMatchingQuery, chunk: TextChunk) -> bool:
        searchable = self._normalize(" ".join(filter(None, (
            chunk.chapter_title,
            chunk.section_title,
            chunk.content,
        ))))
        core = self._normalize(prepared.core_phrase)
        if core and core in searchable:
            return True
        keyword_hits = {
            keyword
            for keyword in prepared.expanded_keywords
            if self._normalize(keyword) in searchable
        }
        return len(keyword_hits) >= 2

    def _requires_strict_short_evidence(self, prepared: PreparedMatchingQuery) -> bool:
        return len(self._normalize(prepared.matching_query)) <= 4

    def _score(self, item: str, syllabus_chapter: str | None, chunk: TextChunk) -> float:
        needle = self._normalize(item)
        if not needle:
            return 0.0
        titles = [self._normalize(value) for value in (chunk.section_title, chunk.chapter_title) if value]
        body = self._normalize(chunk.content)
        if any(needle == title for title in titles):
            base = 1.0
        elif any(needle in title or title in needle for title in titles if title):
            base = 0.92
        elif needle in body:
            base = 0.8
        else:
            comparisons = titles + [body[: max(len(needle) * 6, 160)]]
            sequence = max((SequenceMatcher(None, needle, value).ratio() for value in comparisons), default=0.0)
            overlap = self._ngram_overlap(needle, body)
            base = max(sequence * 0.72, overlap * 0.68)

        if syllabus_chapter and chunk.chapter_title:
            chapter_similarity = SequenceMatcher(
                None, self._normalize(syllabus_chapter), self._normalize(chunk.chapter_title)
            ).ratio()
            base += min(0.08, chapter_similarity * 0.08)
        return min(base, 1.0)

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", value, flags=re.UNICODE).casefold()

    @staticmethod
    def _ngram_overlap(needle: str, haystack: str) -> float:
        size = 2 if len(needle) >= 4 else 1
        grams = {needle[index : index + size] for index in range(len(needle) - size + 1)}
        if not grams:
            return 0.0
        matched = sum(gram in haystack for gram in grams)
        return matched / len(grams)
