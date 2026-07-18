import re
from difflib import SequenceMatcher

from app.matching.preprocessing import MatchingQueryPreprocessor, PreparedMatchingQuery
from app.matching.schemas import CandidateChunk, ItemMatch
from app.models.document import TextChunk


class MatchingService:
    _MAX_GENERATION_EVIDENCE = 4
    _MAX_CONTEXT_CHARACTERS = 12_000

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
        candidates = list(primary)
        if len(candidates) < self._max_candidates:
            fallback = self._build_candidates(
                prepared=prepared,
                scored=scored,
                threshold=self._fallback_threshold,
                require_recall_evidence=True,
            )
            primary_ids = {candidate.chunk_id for candidate in candidates}
            candidates.extend(candidate for candidate in fallback if candidate.chunk_id not in primary_ids)
            if not primary and candidates:
                recall_stage = "secondary"

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

    def select_generation_evidence(
        self,
        *,
        match: ItemMatch,
        chunks: list[TextChunk],
    ) -> list[CandidateChunk]:
        """Build a compact, diverse evidence set without running retrieval again."""
        if not match.candidates:
            return []

        prepared = self._preprocessor.prepare(match.syllabus_item_original)
        by_position = {(chunk.parsed_document_id, chunk.position): chunk for chunk in chunks}
        pool = list(match.candidates)
        seen_ids = {candidate.chunk_id for candidate in pool}

        for candidate in match.candidates:
            source = next((chunk for chunk in chunks if chunk.id == candidate.chunk_id), None)
            if source is None:
                continue
            for position in (source.position - 1, source.position + 1):
                adjacent = by_position.get((source.parsed_document_id, position))
                if adjacent is None or adjacent.id in seen_ids:
                    continue
                if not self._same_chapter(source, adjacent):
                    continue
                adjacent_score = self._score(prepared.matching_query, match.chapter, adjacent)
                if not self._has_adjacent_evidence(prepared, adjacent):
                    continue
                pool.append(self._candidate(prepared, adjacent_score, adjacent))
                seen_ids.add(adjacent.id)

        selected: list[CandidateChunk] = []
        remaining = list(pool)
        context_characters = 0
        while remaining and len(selected) < self._MAX_GENERATION_EVIDENCE:
            ranked = sorted(
                remaining,
                key=lambda candidate: (
                    -self._diversity_score(candidate, selected),
                    candidate.page_start,
                    str(candidate.chunk_id),
                ),
            )
            candidate = ranked[0]
            remaining.remove(candidate)
            if self._duplicates_existing(candidate, selected):
                continue
            if selected and context_characters + len(candidate.text) > self._MAX_CONTEXT_CHARACTERS:
                continue
            selected.append(candidate)
            context_characters += len(candidate.text)
        return selected

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
            candidates.append(self._candidate(prepared, score, chunk))
        return candidates

    @staticmethod
    def _candidate(prepared: PreparedMatchingQuery, score: float, chunk: TextChunk) -> CandidateChunk:
        return CandidateChunk(
            syllabus_item=prepared.syllabus_item_original,
            chunk_id=chunk.id,
            score=round(score, 4),
            chapter=chunk.chapter_title,
            section=chunk.section_title,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            text=chunk.content,
        )

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

    def _has_adjacent_evidence(self, prepared: PreparedMatchingQuery, chunk: TextChunk) -> bool:
        searchable = self._normalize(" ".join(filter(None, (chunk.section_title, chunk.content))))
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
        score = min(base, 1.0)
        if self._is_structural_short_chunk(chunk) and score < 0.9:
            score *= 0.55
        return score

    def _is_structural_short_chunk(self, chunk: TextChunk) -> bool:
        content = self._normalize(chunk.content)
        if len(content) >= 50:
            return False
        titles = self._normalize(" ".join(filter(None, (chunk.chapter_title, chunk.section_title))))
        without_numbering = re.sub(r"^(?:第?[一二三四五六七八九十百\d]+[章节篇部、.]?)+", "", content)
        title_like = bool(titles) and (content in titles or titles in content)
        directory_like = "目录" in content or len(without_numbering) <= 16
        return title_like or directory_like

    @classmethod
    def _same_chapter(cls, first: TextChunk, second: TextChunk) -> bool:
        if not first.chapter_title or not second.chapter_title:
            return False
        return cls._normalize(first.chapter_title) == cls._normalize(second.chapter_title)

    @classmethod
    def _duplicates_existing(cls, candidate: CandidateChunk, selected: list[CandidateChunk]) -> bool:
        normalized = re.sub(r"\s+", " ", candidate.text).strip().casefold()
        for existing in selected:
            other = re.sub(r"\s+", " ", existing.text).strip().casefold()
            if normalized == other:
                return True
            if normalized and other and SequenceMatcher(None, normalized, other).ratio() >= 0.9:
                return True
            overlap = max(0, min(candidate.page_end, existing.page_end) - max(candidate.page_start, existing.page_start) + 1)
            shorter_span = min(candidate.page_end - candidate.page_start + 1, existing.page_end - existing.page_start + 1)
            if overlap / max(shorter_span, 1) >= 0.8 and SequenceMatcher(None, normalized, other).ratio() >= 0.65:
                return True
        return False

    @staticmethod
    def _diversity_score(candidate: CandidateChunk, selected: list[CandidateChunk]) -> float:
        if not selected:
            return candidate.score
        overlaps = [
            max(0, min(candidate.page_end, other.page_end) - max(candidate.page_start, other.page_start) + 1)
            for other in selected
        ]
        page_diversity = 0.08 if not any(overlaps) else -0.12
        detail_bonus = 0.05 if len(candidate.text.strip()) >= 50 else -0.08
        useful_detail = re.search(r"定义|内涵|原理|步骤|流程|因素|例如|例子|案例|计算|公式|方法", candidate.text)
        utility_bonus = 0.06 if useful_detail else 0.0
        return candidate.score + page_diversity + detail_bonus + utility_bonus

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
