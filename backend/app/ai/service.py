import uuid
import json
import logging
from dataclasses import dataclass
from typing import Callable

from app.ai.clients.base import AIClient
from app.ai.prompt_builder import PromptBuilder
from app.ai.prompt_loader import load_prompt, render_prompt
from app.ai.schemas import GeneratedItemResult, GeneratedProject, RetrievalQueryRewrite
from app.ai.validation import (
    AIOutputValidationError,
    salvage_generated_item,
    validate_generated_item,
    validate_generated_project,
)
from app.matching.schemas import CandidateChunk

logger = logging.getLogger("revia.ai.validation")


@dataclass(frozen=True)
class StudyMaterialRequest:
    project_id: uuid.UUID
    project_name: str
    syllabus_text: str
    source_context: str


@dataclass(frozen=True)
class ItemGenerationRequest:
    project_id: uuid.UUID
    project_name: str
    project_description: str | None
    syllabus_chapter: str | None
    syllabus_item: str
    candidates: list[CandidateChunk]


class AIService:
    def __init__(self, client: AIClient, prompt_builder: PromptBuilder | None = None) -> None:
        self._client = client
        self._prompt_builder = prompt_builder or PromptBuilder()

    async def generate_study_material(self, request: StudyMaterialRequest) -> GeneratedProject:
        raw_output = await self._client.generate_completion(
            system_prompt=load_prompt("system.txt"),
            user_prompt=render_prompt(
                "generate_three_versions.txt",
                project_id=str(request.project_id),
                project_name=request.project_name,
                syllabus_text=request.syllabus_text or "未提供文本考纲",
                source_context=request.source_context or "当前阶段未解析 PDF 正文",
            ),
        )
        result = validate_generated_project(raw_output)
        if result.project_id != request.project_id:
            raise AIOutputValidationError("AI output project_id does not match the requested project")
        return result

    async def generate_item(
        self,
        request: ItemGenerationRequest,
        *,
        before_validation: Callable[[], None] | None = None,
    ) -> GeneratedItemResult:
        prompts = self._prompt_builder.build_item(
            project_id=request.project_id,
            project_name=request.project_name,
            project_description=request.project_description,
            syllabus_chapter=request.syllabus_chapter,
            syllabus_item=request.syllabus_item,
            candidates=request.candidates,
        )
        raw_output = await self._client.generate_completion(
            system_prompt=prompts.system_prompt,
            user_prompt=prompts.user_prompt,
        )
        if before_validation:
            before_validation()
        try:
            result = validate_generated_item(raw_output, fallback_title=request.syllabus_item)
            self._validate_sources(result, request.candidates)
        except AIOutputValidationError as first_error:
            logger.warning("AI item validation failed; starting one repair retry: %s", first_error)
            repair = self._prompt_builder.build_repair(
                syllabus_chapter=request.syllabus_chapter,
                syllabus_item=request.syllabus_item,
                candidates=request.candidates,
                raw_output=raw_output,
                validation_error=str(first_error),
            )
            repaired_output = await self._client.generate_completion(
                system_prompt=repair.system_prompt,
                user_prompt=repair.user_prompt,
            )
            try:
                result = validate_generated_item(repaired_output, fallback_title=request.syllabus_item)
                self._validate_sources(result, request.candidates)
                logger.info("AI item structure repair succeeded")
            except AIOutputValidationError as second_error:
                logger.warning("AI item structure repair failed; preserving readable content: %s", second_error)
                try:
                    result = salvage_generated_item(
                        [repaired_output, raw_output],
                        fallback_title=request.syllabus_item,
                        candidates=request.candidates,
                    )
                    self._validate_sources(result, request.candidates)
                    logger.warning("AI item was saved with format warnings: %s", result.format_warnings)
                except AIOutputValidationError as salvage_error:
                    logger.error("AI item deterministic salvage failed: %s", salvage_error)
                    raise AIOutputValidationError(
                        "AI output failed schema validation and contained no readable content after salvage: "
                        + self._safe_validation_reason(salvage_error)
                    ) from second_error
        return result

    async def rewrite_retrieval_queries(
        self,
        *,
        syllabus_item: str,
        hierarchy_context: list[str],
    ) -> list[str]:
        raw_output = await self._client.generate_completion(
            system_prompt=load_prompt("query_rewrite_system.txt"),
            user_prompt=render_prompt(
                "rewrite_retrieval_query.txt",
                syllabus_item=syllabus_item,
                hierarchy_context=json.dumps(hierarchy_context, ensure_ascii=False),
            ),
        )
        try:
            payload = RetrievalQueryRewrite.model_validate(json.loads(raw_output))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("AI retrieval query rewrite was ignored because it failed strict validation: %s", exc)
            return []
        return payload.queries

    @staticmethod
    def _safe_validation_reason(error: AIOutputValidationError) -> str:
        reason = " ".join(str(error).split()) or error.__class__.__name__
        return reason[:900]

    @staticmethod
    def _validate_sources(result: GeneratedItemResult, candidates: list[CandidateChunk]) -> None:
        candidate_ids = {candidate.chunk_id for candidate in candidates}
        allowed_pages = {
            page
            for candidate in candidates
            for page in range(candidate.page_start, candidate.page_end + 1)
        }
        for bullet in result.bullet_points:
            if not set(bullet.source_chunk_ids).issubset(candidate_ids):
                raise AIOutputValidationError("AI output cited a TextChunk that was not supplied")
            if not set(bullet.source_pages).issubset(allowed_pages):
                raise AIOutputValidationError("AI output cited a page outside the supplied TextChunks")
