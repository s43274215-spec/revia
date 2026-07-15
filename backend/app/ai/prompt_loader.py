from functools import lru_cache
from pathlib import Path

PROMPT_DIRECTORY = Path(__file__).parent / "prompts"


@lru_cache
def load_prompt(name: str) -> str:
    path = PROMPT_DIRECTORY / name
    if path.parent != PROMPT_DIRECTORY or not path.is_file():
        raise FileNotFoundError(f"Prompt not found: {name}")
    return path.read_text(encoding="utf-8").strip()


def render_prompt(name: str, **values: str) -> str:
    return load_prompt(name).format(**values)
