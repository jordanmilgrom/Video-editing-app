"""Shared response envelope and input validators for MCP tools.

Every non-vision tool returns a `ToolResponse`. Vision tools return MCP
image content on success and `ToolResponse` on failure. No raw
exceptions cross the protocol boundary.

Error codes (stable, lowercase_with_underscores):

  relative_path        an input path was not absolute
  not_a_directory      folder doesn't exist or isn't a directory
  not_a_file           file doesn't exist or isn't a regular file
  ffprobe_unavailable  ffprobe not on PATH
  ffmpeg_unavailable   ffmpeg not on PATH
  invalid_clip         clip has no video stream / fails to probe
  invalid_spec         SequenceSpec or Transcript shape rejected
  internal_error       any other unexpected failure (logged via stderr)
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from roughcut_core.models import (
    ClipMeta,
    ScriptAlignment,
    TakeCluster,
    Transcript,
)


class ToolResponse(BaseModel):
    """Single envelope shape every tool's success/failure path emits."""

    ok: bool
    clips: list[ClipMeta] = Field(default_factory=list)
    transcript: Transcript | None = None
    clusters: list[TakeCluster] = Field(default_factory=list)
    alignments: list[ScriptAlignment] = Field(default_factory=list)
    output_path: str | None = None
    summary: dict | None = None
    error: str | None = None
    message: str | None = None


def cache_dir(override: str | None) -> Path:
    """Resolve cache directory: explicit arg > env var > ~/.cache/roughcut."""
    if override:
        return Path(override)
    env = os.getenv("ROUGHCUT_CACHE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "roughcut"


def abs_dir(p: str) -> Path | ToolResponse:
    path = Path(p)
    if not path.is_absolute():
        return ToolResponse(ok=False, error="relative_path",
                            message=f"Absolute path required, got: {p!r}")
    if not path.is_dir():
        return ToolResponse(ok=False, error="not_a_directory", message=str(path))
    return path


def abs_file(p: str) -> Path | ToolResponse:
    path = Path(p)
    if not path.is_absolute():
        return ToolResponse(ok=False, error="relative_path",
                            message=f"Absolute path required, got: {p!r}")
    if not path.is_file():
        return ToolResponse(ok=False, error="not_a_file", message=str(path))
    return path


def abs_path(p: str) -> Path | ToolResponse:
    """For paths that may not exist yet (e.g. the FCPXML output target)."""
    path = Path(p)
    if not path.is_absolute():
        return ToolResponse(ok=False, error="relative_path",
                            message=f"Absolute path required, got: {p!r}")
    return path


def to_error(exc: Exception, default: str = "internal_error") -> ToolResponse:
    """Map a known exception to the stable error envelope.

    Tools wrap their core delegation in `try/except Exception as e` and
    call this once; the message comes from `str(exc)`.
    """
    if isinstance(exc, NotADirectoryError):
        code = "not_a_directory"
    elif isinstance(exc, FileNotFoundError):
        code = "not_a_file"
    elif isinstance(exc, RuntimeError):
        code = "ffprobe_unavailable" if "ffprobe" in str(exc) else "ffmpeg_unavailable"
    elif isinstance(exc, ValueError):
        code = "invalid_clip"
    else:
        code = default
    msg = str(exc) if code != "internal_error" else f"{type(exc).__name__}: {exc}"
    return ToolResponse(ok=False, error=code, message=msg)
