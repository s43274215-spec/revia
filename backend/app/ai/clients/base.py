from abc import ABC, abstractmethod


class AIClientError(RuntimeError):
    pass


class AIConfigurationError(AIClientError):
    pass


class AIRequestError(AIClientError):
    pass


class AIClient(ABC):
    @abstractmethod
    async def generate_completion(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return one model response as a string."""
        raise NotImplementedError
