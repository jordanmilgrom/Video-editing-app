"""anthropic SDK wrapper.

Loads markdown prompts from `roughcut/prompts/`, runs Claude tool-use calls
for structured JSON output, retries with exponential backoff. Implementation
lands alongside the first module that needs it (takes.py).
"""

from __future__ import annotations

from pathlib import Path

MODEL = "claude-sonnet-4-6"
PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """Read a prompt markdown file by stem (e.g. 'pick_best_take')."""
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def call(prompt_name: str, schema: dict, **template_vars: object) -> dict:
    """Run a Claude tool-use call and return the parsed JSON payload."""
    raise NotImplementedError("claude.call lands with takes.py")


def call_vision(prompt_name: str, image_path: Path, schema: dict, **template_vars: object) -> dict:
    """Run a Claude vision tool-use call against a local image."""
    raise NotImplementedError("claude.call_vision lands with broll.py")
