import json
import re
from difflib import SequenceMatcher

from app.matching.aliases import normalize_query_key
from app.matching.preprocessing import MatchingQueryPreprocessor, PreparedMatchingQuery
from app.matching.query_planning import QueryPlan, QueryPlanner, SyllabusItemType
from app.matching.schemas import CandidateChunk, ItemMatch
from app.models.document import TextChunk
from app.syllabus.parser import ParsedSyllabusItem


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
        query_planner: QueryPlanner | None = None,
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
        self._query_planner = query_planner or QueryPlanner(self._preprocessor)

    def plan_items(self, entries: list[ParsedSyllabusItem]) -> list[QueryPlan]:
        return self._query_planner.plan_items(entries)

    def match_item(
        self,
        *,
        syllabus_item: str,
        syllabus_chapter: str | None,
        chunks: list[TextChunk],
    ) -> ItemMatch:
        plan = self._query_planner.plan_item(syllabus_item, chapter=syllabus_chapter)
        return self.match_plan(plan=plan, chunks=chunks)

    def match_plan(self, *, plan: QueryPlan, chunks: list[TextChunk]) -> ItemMatch:
        if plan.item_type == SyllabusItemType.TASK and not plan.retrieval_queries:
            return self._empty_match(plan, {}, "related_theory_pending")

        by_query: dict[str, list[CandidateChunk]] = {}
        top_scores: dict[str, float] = {}
        stages: list[str] = []
        stages_by_query: dict[str, str] = {}
        for query in plan.retrieval_queries:
            candidates, top_score, stage = self._retrieve_query(
                query=query,
                original_item=plan.original_query,
                syllabus_chapter=plan.hierarchy_context.chapter,
                chunks=chunks,
            )
            by_query[query] = candidates
            top_scores[query] = round(top_score, 4)
            if stage:
                stages.append(stage)
                stages_by_query[query] = stage

        limited = self._fuse_query_candidates(plan, by_query)
        if not limited:
            maximum = max(top_scores.values(), default=0.0)
            category = "below_threshold" if maximum < self._fallback_threshold else "insufficient_lexical_evidence"
            return self._empty_match(plan, top_scores, category)
        return ItemMatch(
            syllabus_item=plan.original_query,
            syllabus_item_original=plan.original_query,
            matching_query=self._preprocessor.prepare(plan.original_query).matching_query,
            chapter=plan.hierarchy_context.chapter,
            matched=True,
            candidates=limited,
            query_plan=plan,
            query_top_scores=top_scores,
            used_ai_fallback=plan.used_ai_fallback,
            recall_stage=stages_by_query.get(plan.original_query) or ("primary" if "primary" in stages else "secondary"),
        )

    def resolve_dependent_matches(self, plans: list[QueryPlan], matches: list[ItemMatch]) -> list[ItemMatch]:
        resolved = list(matches)
        indexes_by_title = {normalize_query_key(plan.original_query): index for index, plan in enumerate(plans)}
        for index, plan in enumerate(plans):
            if resolved[index].matched and plan.item_type != SyllabusItemType.COLLECTION:
                continue
            source_titles: tuple[str, ...] = ()
            failure_category: str | None = None
            if plan.item_type == SyllabusItemType.COLLECTION and plan.hierarchy_context.child_titles:
                source_titles = plan.hierarchy_context.child_titles
                failure_category = "no_child_evidence"
            elif plan.item_type == SyllabusItemType.TASK:
                source_titles = plan.hierarchy_context.related_titles
                failure_category = "no_related_theory_evidence"
            else:
                continue

            source_matches = [
                resolved[source_index]
                for title in source_titles
                if (source_index := indexes_by_title.get(normalize_query_key(title))) is not None
                and resolved[source_index].matched
            ]
            if plan.item_type == SyllabusItemType.COLLECTION and resolved[index].matched:
                source_matches.insert(0, resolved[index])
            if not source_matches:
                resolved[index] = self._empty_match(plan, {}, failure_category or "no_reusable_evidence")
                continue
            candidates = self._fuse_reused_candidates(plan, source_matches)
            top_scores = {
                query: score
                for source in source_matches
                for query, score in source.query_top_scores.items()
            }
            resolved[index] = ItemMatch(
                syllabus_item=plan.original_query,
                syllabus_item_original=plan.original_query,
                matching_query="reused_hierarchy_evidence",
                chapter=plan.hierarchy_context.chapter,
                matched=bool(candidates),
                candidates=candidates,
                query_plan=plan,
                query_top_scores=top_scores,
                used_ai_fallback=False,
                unmatched_reason_category=None if candidates else failure_category,
                recall_stage="primary" if candidates else None,
                reason=None if candidates else "unmatched: no reusable hierarchy evidence",
            )
        return resolved

    def _retrieve_query(
        self,
        *,
        query: str,
        original_item: str,
        syllabus_chapter: str | None,
        chunks: list[TextChunk],
    ) -> tuple[list[CandidateChunk], float, str | None]:
        prepared = self._preprocessor.prepare(query)
        scored: list[tuple[float, TextChunk]] = []
        for chunk in chunks:
            score = self._score(prepared.matching_query, syllabus_chapter, chunk)
            if self._requires_strict_short_evidence(prepared) and not self._has_recall_evidence(
                prepared, chunk, include_chapter=False
            ):
                continue
            scored.append((score, chunk))
        top_score = max((score for score, _ in scored), default=0.0)

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

        candidates = [candidate.model_copy(update={
            "syllabus_item": original_item,
            "matched_queries": [query],
        }) for candidate in candidates]
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.page_start, str(candidate.chunk_id)))
        return candidates[: self._max_candidates], top_score, recall_stage

    def _empty_match(self, plan: QueryPlan, top_scores: dict[str, float], category: str) -> ItemMatch:
        return ItemMatch(
            syllabus_item=plan.original_query,
            syllabus_item_original=plan.original_query,
            matching_query=plan.original_query,
            chapter=plan.hierarchy_context.chapter,
            matched=False,
            candidates=[],
            query_plan=plan,
            query_top_scores=top_scores,
            used_ai_fallback=plan.used_ai_fallback,
            unmatched_reason_category=category,
            reason="unmatched: no TextChunk met the configured relevance threshold",
        )

    def _fuse_query_candidates(
        self,
        plan: QueryPlan,
        by_query: dict[str, list[CandidateChunk]],
    ) -> list[CandidateChunk]:
        fused: dict[object, CandidateChunk] = {}
        for query, candidates in by_query.items():
            for candidate in candidates:
                existing = fused.get(candidate.chunk_id)
                if existing is None:
                    fused[candidate.chunk_id] = candidate
                    continue
                queries = list(dict.fromkeys((*existing.matched_queries, query)))
                fused[candidate.chunk_id] = existing.model_copy(update={
                    "score": max(existing.score, candidate.score),
                    "matched_queries": queries,
                })

        ranked = sorted(fused.values(), key=lambda candidate: (-candidate.score, candidate.page_start, str(candidate.chunk_id)))
        selected: list[CandidateChunk] = []
        if plan.subqueries:
            uncovered = {normalize_query_key(query) for query in plan.subqueries}
            while uncovered and len(selected) < self._max_candidates:
                eligible = [
                    candidate
                    for candidate in ranked
                    if candidate.chunk_id not in {item.chunk_id for item in selected}
                    and uncovered.intersection(normalize_query_key(query) for query in candidate.matched_queries)
                ]
                if not eligible:
                    break
                candidate = max(
                    eligible,
                    key=lambda item: (
                        len(uncovered.intersection(normalize_query_key(query) for query in item.matched_queries)),
                        item.score,
                        -item.page_start,
                    ),
                )
                selected.append(candidate)
                uncovered.difference_update(normalize_query_key(query) for query in candidate.matched_queries)
        selected.extend(candidate for candidate in ranked if candidate.chunk_id not in {item.chunk_id for item in selected})
        return selected[: self._max_candidates]

    def _fuse_reused_candidates(self, plan: QueryPlan, sources: list[ItemMatch]) -> list[CandidateChunk]:
        by_query: dict[str, list[CandidateChunk]] = {}
        for source in sources:
            by_query[source.syllabus_item_original] = [candidate.model_copy(update={
                "syllabus_item": plan.original_query,
                "matched_queries": list(dict.fromkeys((*candidate.matched_queries, source.syllabus_item_original))),
            }) for candidate in source.candidates]
        return self._fuse_query_candidates(plan, by_query)

    @staticmethod
    def failure_diagnostic(match: ItemMatch) -> str:
        payload = {
            "item_type": match.query_plan.item_type.value,
            "queries": [query[:80] for query in match.query_plan.retrieval_queries],
            "top_scores": {query[:80]: score for query, score in match.query_top_scores.items()},
            "reason_category": match.unmatched_reason_category or "unmatched",
            "used_ai_fallback": match.used_ai_fallback,
        }
        return "unmatched: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def select_generation_evidence(
        self,
        *,
        match: ItemMatch,
        chunks: list[TextChunk],
    ) -> list[CandidateChunk]:
        """Build a compact, diverse evidence set without running retrieval again."""
        if not match.candidates:
            return []

        by_position = {(chunk.parsed_document_id, chunk.position): chunk for chunk in chunks}
        pool = list(match.candidates)
        subquery_keys = {normalize_query_key(query) for query in match.query_plan.subqueries}
        covered_subquery_candidates = [
            candidate
            for candidate in pool
            if subquery_keys.intersection(normalize_query_key(query) for query in candidate.matched_queries)
        ]
        if covered_subquery_candidates:
            pool = covered_subquery_candidates
        seen_ids = {candidate.chunk_id for candidate in pool}

        for candidate in list(pool):
            source = next((chunk for chunk in chunks if chunk.id == candidate.chunk_id), None)
            if source is None:
                continue
            for position in (source.position - 1, source.position + 1):
                adjacent = by_position.get((source.parsed_document_id, position))
                if adjacent is None or adjacent.id in seen_ids:
                    continue
                if not self._same_chapter(source, adjacent):
                    continue
                queries = candidate.matched_queries or list(match.query_plan.retrieval_queries)
                prepared_queries = [self._preprocessor.prepare(query) for query in queries]
                matching_prepared = [prepared for prepared in prepared_queries if self._has_adjacent_evidence(prepared, adjacent)]
                if not matching_prepared:
                    continue
                adjacent_score = max(self._score(prepared.matching_query, match.chapter, adjacent) for prepared in matching_prepared)
                pool.append(self._candidate(matching_prepared[0], adjacent_score, adjacent).model_copy(update={
                    "syllabus_item": match.syllabus_item_original,
                    "matched_queries": [prepared.syllabus_item_original for prepared in matching_prepared],
                }))
                seen_ids.add(adjacent.id)

        selected: list[CandidateChunk] = []
        remaining = list(pool)
        context_characters = 0
        uncovered = {normalize_query_key(query) for query in match.query_plan.subqueries}
        while remaining and len(selected) < self._MAX_GENERATION_EVIDENCE:
            ranked = sorted(
                remaining,
                key=lambda candidate: (
                    -(self._diversity_score(candidate, selected) + (
                        0.14 if uncovered.intersection(normalize_query_key(query) for query in candidate.matched_queries) else 0.0
                    )),
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
            uncovered.difference_update(normalize_query_key(query) for query in candidate.matched_queries)
            if match.query_plan.subqueries and not uncovered and len(selected) >= 2:
                break
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
            if not require_recall_evidence and score < 0.8:
                searchable = self._normalize(" ".join(filter(None, (
                    chunk.chapter_title,
                    chunk.section_title,
                    chunk.content,
                ))))
                if self._ngram_overlap(self._normalize(prepared.matching_query), searchable) < 0.6:
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

    def _has_recall_evidence(
        self,
        prepared: PreparedMatchingQuery,
        chunk: TextChunk,
        *,
        include_chapter: bool = True,
    ) -> bool:
        searchable = self._normalize(" ".join(filter(None, (
            chunk.chapter_title if include_chapter else None,
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
        if score < 0.9 and re.search(r"qkc:/|https?://|复制此链接", chunk.content, flags=re.IGNORECASE):
            score *= 0.75
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
            detail_bonus = 0.05 if len(candidate.text.strip()) >= 50 else -0.08
            useful_detail = re.search(r"定义|内涵|原理|步骤|流程|因素|例如|例子|案例|计算|公式|方法", candidate.text)
            utility_bonus = 0.06 if useful_detail else 0.0
            return candidate.score + detail_bonus + utility_bonus
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
        return normalize_query_key(value)

    @staticmethod
    def _ngram_overlap(needle: str, haystack: str) -> float:
        size = 2 if len(needle) >= 4 else 1
        grams = {needle[index : index + size] for index in range(len(needle) - size + 1)}
        if not grams:
            return 0.0
        matched = sum(gram in haystack for gram in grams)
        return matched / len(grams)
