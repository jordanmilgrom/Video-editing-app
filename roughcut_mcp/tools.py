"""MCP tool wrappers.

Each tool:
- validates inputs (absolute paths only),
- delegates to `roughcut_core`,
- returns a single `ToolResponse` envelope (never raises across the
  MCP boundary).

Errors use short machine-readable codes so the agent can branch
deterministically; `message` carries the human-readable detail.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from roughcut_core import clips
from roughcut_core.models import ClipMeta

log = logging.getLogger("roughcut_mcp.tools")


class ToolResponse(BaseModel):
    """Discriminated envelope for every tool return.

    `ok=True` → payload fields populated; `ok=False` → `error` + `message`.
    """

    ok: bool
    clips: list[ClipMeta] = Field(default_factory=list)
    error: str | None = None
    message: str | None = None


_LIST_CLIPS_DOC = (
    "Inventory the video files in a folder via ffprobe and return their "
    "codec, duration, frame rate, resolution, and size.\n\n"
    "Call this FIRST when starting a rough cut, before transcribing or "
    "frame-grabbing — it's cheap, read-only, and tells you what footage "
    "exists. Use it on the interview folder and the b-roll folder "
    "separately so you can plan the cut.\n\n"
    "`folder` MUST be an absolute path. Relative paths are rejected with "
    "`error=\"relative_path\"` so the agent can re-ask the user. Recursion "
    "is on by default; set `recursive=False` to scan only the top level."
)


def register_tools(mcp: FastMCP) -> None:
    """Register every roughcut tool on the given FastMCP server."""

    @mcp.tool(description=_LIST_CLIPS_DOC)
    def list_clips(folder: str, recursive: bool = True) -> ToolResponse:
        return _list_clips(folder, recursive)


def _list_clips(folder: str, recursive: bool) -> ToolResponse:
    """Implementation split out so tests can call it without FastMCP."""
    path = Path(folder)
    if not path.is_absolute():
        return ToolResponse(
            ok=False,
            error="relative_path",
            message=f"Absolute path required, got: {folder!r}",
        )
    try:
        result = clips.list_clips(path, recursive=recursive)
    except NotADirectoryError as e:
        return ToolResponse(ok=False, error="not_a_directory", message=str(e))
    except FileNotFoundError as e:
        return ToolResponse(ok=False, error="not_a_directory", message=str(e))
    except RuntimeError as e:
        # roughcut_core raises RuntimeError when ffprobe is missing from PATH.
        return ToolResponse(ok=False, error="ffprobe_unavailable", message=str(e))
    except ValueError as e:
        # e.g. a file in the folder had no video stream.
        return ToolResponse(ok=False, error="invalid_clip", message=str(e))
    except Exception as e:  # noqa: BLE001 — last-resort wall
        log.exception("list_clips failed unexpectedly")
        return ToolResponse(
            ok=False, error="internal_error", message=f"{type(e).__name__}: {e}"
        )
    return ToolResponse(ok=True, clips=result)
