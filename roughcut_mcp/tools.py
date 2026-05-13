"""MCP tool registrations and implementations.

Each implementation is a small function exported under a `_*` name so
tests can call it directly (without spinning up FastMCP).

Validation, response envelopes, and error mapping live in
`roughcut_mcp.responses`. Tool descriptions live in
`roughcut_mcp.descriptions`. Nothing here imports ffmpeg/whisper/Pillow.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image
from mcp.types import TextContent

from roughcut_core import broll, clips, fcpxml, takes, transcribe
from roughcut_core.models import SequenceSpec, Transcript
from roughcut_mcp import descriptions as desc
from roughcut_mcp.responses import (
    ToolResponse,
    abs_dir,
    abs_file,
    abs_path,
    cache_dir,
    to_error,
)

log = logging.getLogger("roughcut_mcp.tools")


def register_tools(mcp: FastMCP) -> None:
    """Register every roughcut tool on the given FastMCP server."""

    @mcp.tool(description=desc.LIST_CLIPS)
    def list_clips(folder: str, recursive: bool = True) -> ToolResponse:
        return _list_clips(folder, recursive)

    @mcp.tool(description=desc.TRANSCRIBE_VIDEO)
    def transcribe_video(
        video_path: str,
        language: str = "auto",
        model: str = transcribe.DEFAULT_WHISPER_MODEL,
        cache_dir: str | None = None,
    ) -> ToolResponse:
        return _transcribe_video(video_path, language, model, cache_dir)

    @mcp.tool(description=desc.CLUSTER_TAKES_BY_SILENCE)
    def cluster_takes_by_silence(
        transcript: dict, silence_threshold_sec: float = 2.0
    ) -> ToolResponse:
        return _cluster_takes_by_silence(transcript, silence_threshold_sec)

    @mcp.tool(description=desc.ALIGN_TAKES_TO_SCRIPT)
    def align_takes_to_script(transcript: dict, script_text: str) -> ToolResponse:
        return _align_takes_to_script(transcript, script_text)

    @mcp.tool(description=desc.GENERATE_FCPXML)
    def generate_fcpxml(sequence_spec: dict, output_path: str) -> ToolResponse:
        return _generate_fcpxml(sequence_spec, output_path)

    @mcp.tool(description=desc.EXTRACT_FRAME_GRID)
    def extract_frame_grid(
        video_path: str,
        num_frames: int = 16,
        overlay_timecodes: bool = True,
        cache_dir: str | None = None,
    ) -> Any:
        """Returns `[Image, TextContent]` on success, `ToolResponse` on failure."""
        return _extract_frame_grid(video_path, num_frames, overlay_timecodes, cache_dir)

    @mcp.tool(description=desc.GET_CLIP_THUMBNAIL)
    def get_clip_thumbnail(video_path: str, timecode_sec: float) -> Any:
        """Returns `[Image, TextContent]` on success, `ToolResponse` on failure."""
        return _get_clip_thumbnail(video_path, timecode_sec)

    @mcp.tool(description=desc.GET_PROJECT_PATHS)
    def get_project_paths() -> ToolResponse:
        return _get_project_paths()


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


def _list_clips(folder: str, recursive: bool) -> ToolResponse:
    path = abs_dir(folder)
    if isinstance(path, ToolResponse):
        return path
    try:
        return ToolResponse(ok=True, clips=clips.list_clips(path, recursive=recursive))
    except Exception as e:  # noqa: BLE001
        log.exception("list_clips failed")
        return to_error(e)


def _transcribe_video(
    video_path: str, language: str, model: str, cache_dir_override: str | None
) -> ToolResponse:
    path = abs_file(video_path)
    if isinstance(path, ToolResponse):
        return path
    lang = None if not language or language.lower() == "auto" else language
    try:
        t = transcribe.transcribe(
            path, cache_dir(cache_dir_override), model=model, language=lang
        )
    except Exception as e:  # noqa: BLE001
        log.exception("transcribe_video failed")
        return to_error(e)
    return ToolResponse(ok=True, transcript=t)


def _cluster_takes_by_silence(transcript_data: dict, threshold: float) -> ToolResponse:
    try:
        t = Transcript.model_validate(transcript_data)
    except Exception as e:  # noqa: BLE001
        return ToolResponse(ok=False, error="invalid_spec",
                            message=f"transcript shape invalid: {e}")
    try:
        return ToolResponse(ok=True, clusters=takes.cluster_by_silence(t, threshold))
    except Exception as e:  # noqa: BLE001
        log.exception("cluster_takes_by_silence failed")
        return to_error(e)


def _align_takes_to_script(transcript_data: dict, script_text: str) -> ToolResponse:
    try:
        t = Transcript.model_validate(transcript_data)
    except Exception as e:  # noqa: BLE001
        return ToolResponse(ok=False, error="invalid_spec",
                            message=f"transcript shape invalid: {e}")
    try:
        return ToolResponse(ok=True, alignments=takes.align_to_script(t, script_text))
    except Exception as e:  # noqa: BLE001
        log.exception("align_takes_to_script failed")
        return to_error(e)


def _generate_fcpxml(sequence_spec_data: dict, output_path: str) -> ToolResponse:
    out = abs_path(output_path)
    if isinstance(out, ToolResponse):
        return out
    try:
        spec = SequenceSpec.model_validate(sequence_spec_data)
    except Exception as e:  # noqa: BLE001
        return ToolResponse(ok=False, error="invalid_spec",
                            message=f"SequenceSpec invalid: {e}")
    if not spec.aroll:
        return ToolResponse(ok=False, error="invalid_spec",
                            message="SequenceSpec.aroll is empty; need at least one segment")
    try:
        fcpxml.write_fcpxml(spec, out)
    except Exception as e:  # noqa: BLE001
        log.exception("generate_fcpxml failed")
        return to_error(e)
    return ToolResponse(
        ok=True,
        output_path=str(out),
        summary={
            "aroll_count": len(spec.aroll),
            "broll_count": len(spec.broll),
            "duration_sec": round(sum(s.out_sec - s.in_sec for s in spec.aroll), 3),
        },
    )


def _extract_frame_grid(
    video_path: str, num_frames: int, overlay_timecodes: bool, cache_dir_override: str | None,
) -> Any:
    path = abs_file(video_path)
    if isinstance(path, ToolResponse):
        return path
    try:
        meta = clips.probe_clip(path)
        sheet_dir = cache_dir(cache_dir_override) / "broll-grids"
        key = transcribe.cache_key(path)
        sheet_path = sheet_dir / f"{key}_{num_frames}_{int(overlay_timecodes)}.png"
        if not sheet_path.exists():
            broll.build_contact_sheet(
                path, sheet_path, duration=meta.duration_sec,
                overlay_timecodes=overlay_timecodes, num_frames=num_frames,
            )
    except Exception as e:  # noqa: BLE001
        log.exception("extract_frame_grid failed")
        return to_error(e)
    sidecar = json.dumps({"image_path": str(sheet_path),
                          "duration_sec": meta.duration_sec,
                          "num_frames": num_frames})
    return [Image(path=str(sheet_path)), TextContent(type="text", text=sidecar)]


def _get_clip_thumbnail(video_path: str, timecode_sec: float) -> Any:
    path = abs_file(video_path)
    if isinstance(path, ToolResponse):
        return path
    if timecode_sec < 0:
        return ToolResponse(ok=False, error="invalid_spec",
                            message=f"timecode_sec must be >= 0, got {timecode_sec}")
    try:
        thumb_dir = cache_dir(None) / "thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        key = transcribe.cache_key(path)
        thumb_path = thumb_dir / f"{key}_{int(timecode_sec * 1000)}.jpg"
        if not thumb_path.exists():
            broll.extract_frame(path, timecode_sec, thumb_path)
    except Exception as e:  # noqa: BLE001
        log.exception("get_clip_thumbnail failed")
        return to_error(e)
    sidecar = json.dumps({"image_path": str(thumb_path), "timecode_sec": timecode_sec})
    return [Image(path=str(thumb_path)), TextContent(type="text", text=sidecar)]


def _get_project_paths() -> ToolResponse:
    return ToolResponse(
        ok=True,
        summary={
            "interview_folder": os.environ.get("ROUGHCUT_INTERVIEW_DIR") or None,
            "broll_folder": os.environ.get("ROUGHCUT_BROLL_DIR") or None,
            "script_path": os.environ.get("ROUGHCUT_SCRIPT_PATH") or None,
        },
    )
