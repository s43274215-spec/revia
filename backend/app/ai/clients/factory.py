from app.ai.clients.base import AIClient, AIConfigurationError
from app.ai.clients.deepseek import DeepSeekClient
from app.ai.clients.mock import MockAIClient
from app.core.config import Settings


def build_ai_client(settings: Settings, api_key: str | None = None) -> AIClient:
    if settings.ai_mode == "mock":
        return MockAIClient()
    if not api_key:
        raise AIConfigurationError("当前匿名工作区尚未配置 DeepSeek API Key")
    if settings.ai_provider.casefold() != "deepseek":
        raise AIConfigurationError(f"Unsupported AI provider: {settings.ai_provider}")
    return DeepSeekClient(
        api_key=api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout_seconds=settings.ai_timeout_seconds,
        max_output_tokens=settings.ai_max_output_tokens,
        temperature=settings.ai_temperature,
    )
