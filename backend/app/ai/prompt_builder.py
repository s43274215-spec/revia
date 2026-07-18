import json
import re
import uuid
from dataclasses import dataclass

from app.ai.prompt_loader import render_prompt
from app.matching.schemas import CandidateChunk


@dataclass(frozen=True)
class PromptPair:
    system_prompt: str
    user_prompt: str


class PromptBuilder:
    @staticmethod
    def _source_context(candidates: list[CandidateChunk]) -> str:
        unique_candidates = PromptBuilder._deduplicate_candidates(candidates)
        return json.dumps(
            [
                {
                    "chunk_id": str(candidate.chunk_id),
                    "chapter": candidate.chapter,
                    "section": candidate.section,
                    "page_start": candidate.page_start,
                    "page_end": candidate.page_end,
                    "text": candidate.text,
                }
                for candidate in unique_candidates
            ],
            ensure_ascii=False,
        )

    def build_item(
        self,
        *,
        project_id: uuid.UUID,
        project_name: str,
        project_description: str | None,
        syllabus_chapter: str | None,
        syllabus_item: str,
        candidates: list[CandidateChunk],
    ) -> PromptPair:
        source_context = self._source_context(candidates)
        return PromptPair(
            system_prompt=render_prompt("item_system.txt"),
            user_prompt=render_prompt(
                "generate_item.txt",
                project_name=project_name,
                syllabus_chapter=syllabus_chapter or "未识别章节",
                syllabus_item=syllabus_item,
                source_context=source_context,
            ),
        )

    @staticmethod
    def _deduplicate_candidates(candidates: list[CandidateChunk]) -> list[CandidateChunk]:
        unique: list[CandidateChunk] = []
        seen_text: set[str] = set()
        for candidate in candidates:
            normalized_text = re.sub(r"\s+", " ", candidate.text).strip()
            if normalized_text in seen_text:
                continue
            seen_text.add(normalized_text)
            unique.append(candidate)
        return unique

    def build_repair(
        self,
        *,
        syllabus_chapter: str | None,
        syllabus_item: str,
        candidates: list[CandidateChunk],
        raw_output: str,
        validation_error: str,
    ) -> PromptPair:
        return PromptPair(
            system_prompt=render_prompt("item_system.txt"),
            user_prompt=render_prompt(
                "repair_item_json.txt",
                syllabus_chapter=syllabus_chapter or "未识别章节",
                syllabus_item=syllabus_item,
                source_context=self._source_context(candidates),
                validation_error=validation_error,
                raw_output=raw_output,
            ),
        )
