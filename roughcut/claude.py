"""anthropic SDK wrapper.

- Loads markdown prompts from `roughcut/prompts/`.
- Runs Claude tool-use calls so every output is structured JSON (no
  freeform parsing).
- Retries with exponential backoff on transient API errors.
- Lazy client construction: importing this module does NOT require
  ANTHROPIC_API_KEY (so tests can monkeypatch `call` without an env var).
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_TOOL_NAME = "output"
MAX_RETRIES = 3  # retries after the initial attempt (so up to 4 total)
BASE_BACKOFF = 2.0  # seconds; 2, 4, 8 for attempts 1..3
RETRY_STATUS_CODES = {429, 503, 529}

_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        import anthropic  # imported lazily — needs API key

        _client = anthropic.Anthropic()
    return _client


def load_prompt(name: str, **template_vars: object) -> str:
    """Read a prompt markdown file and substitute `{{var}}` placeholders."""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in template_vars.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def _text_content(prompt: str) -> list[dict]:
    return [{"type": "text", "text": prompt}]


def _image_content(image_path: Path, prompt: str) -> list[dict]:
    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    suffix = image_path.suffix.lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "image/png")
    return [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
        {"type": "text", "text": prompt},
    ]


def _is_retryable(exc: Exception) -> bool:
    """True for 429/503/529 status codes, timeouts, and connection errors."""
    import anthropic

    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in RETRY_STATUS_CODES
    return False


def _invoke(content: list[dict], schema: dict, tool_name: str) -> dict:
    """One Claude call with forced tool-use; returns the tool input dict.

    Retries on 429/503/529, timeouts, and connection errors with
    exponential backoff: 2s, 4s, 8s. Other errors propagate immediately.
    """
    client = _get_client()
    tools = [{"name": tool_name, "description": "Return structured output.", "input_schema": schema}]
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                tools=tools,
                tool_choice={"type": "tool", "name": tool_name},
                messages=[{"role": "user", "content": content}],
            )
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                    return dict(block.input)
            raise RuntimeError(f"Claude response missing tool_use for {tool_name!r}")
        except Exception as exc:
            if not _is_retryable(exc) or attempt == MAX_RETRIES:
                raise
            time.sleep(BASE_BACKOFF ** (attempt + 1))
    raise RuntimeError("unreachable")


def call(prompt_name: str, schema: dict, *, tool_name: str = DEFAULT_TOOL_NAME, **template_vars: object) -> dict:
    """Text-only Claude tool-use call."""
    prompt = load_prompt(prompt_name, **template_vars)
    return _invoke(_text_content(prompt), schema, tool_name)


def call_vision(
    prompt_name: str,
    image_path: Path,
    schema: dict,
    *,
    tool_name: str = DEFAULT_TOOL_NAME,
    **template_vars: object,
) -> dict:
    """Image + text Claude tool-use call."""
    prompt = load_prompt(prompt_name, **template_vars)
    return _invoke(_image_content(image_path, prompt), schema, tool_name)
