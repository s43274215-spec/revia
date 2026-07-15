import json
import logging

from pydantic import ValidationError

from app.ai.schemas import GeneratedItemResult, GeneratedProject

logger = logging.getLogger("revia.ai.validation")


class AIOutputValidationError(ValueError):
    pass


def validate_generated_project(raw_output: str) -> GeneratedProject:
    try:
        payload = json.loads(raw_output)
        return GeneratedProject.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AIOutputValidationError("AI output does not match the Revia learning material schema") from exc


def extract_json(raw_output: str) -> object:
    value = raw_output.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end < start:
        logger.error("AI JSON extraction failed: no JSON object was found")
        raise AIOutputValidationError("AI output did not contain a JSON object")
    try:
        return json.loads(value[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.error("AI JSON extraction failed at line=%d column=%d: %s", exc.lineno, exc.colno, exc.msg)
        raise AIOutputValidationError("AI output contained invalid JSON") from exc


def validate_generated_item(raw_output: str) -> GeneratedItemResult:
    try:
        return GeneratedItemResult.model_validate(extract_json(raw_output))
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False)[:8]
        )
        logger.error(
            "AI item Pydantic validation failed: %s",
            json.dumps(exc.errors(include_url=False), ensure_ascii=False, default=str),
        )
        raise AIOutputValidationError(
            f"AI output does not match the three-version item schema: {details}"
        ) from exc
