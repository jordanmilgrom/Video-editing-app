"""MCP tool registrations + implementations.

Two design points the agent should keep in mind reading this file:

1. **Size-bounded returns.** Real podcast transcripts and per-line script
   alignments routinely exceed Claude Desktop's 1 MB tool-result cap.
   Every tool that can grow large writes its full payload to disk under
   `cache_dir` and returns a small summary containing a `*_path` field
   the next tool consumes. The agent never receives raw transcripts or
   alignments inline.

2. **Two modes side by side.** Doc / interview tools and multicam /
   podcast tools share `transcribe_video` but otherwise live in
   separate name spaces (`mc_*`). Don't mix them in one rough cut.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image
from mcp.types import TextContent

from roughcut_core import broll, cache_io, clips, fcpxml, multicam, takes, transcribe
from roughcut_core.models import AngleSelection, MulticamGroup, SequenceSpec, SpeakerLabel, Transcript
from roughcut_mcp import descriptions as desc
from roughcut_mcp.responses import ToolResponse, abs_dir, abs_file, abs_path, cache_dir, to_error

log = logging.getLogger("roughcut_mcp.tools")


def register_tools(mcp: FastMCP) -> None:
    """Register every roughcut tool on the given FastMCP server."""

    # ----- Inventory + transcription (shared by both modes) ------------------

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

    # ----- Doc / interview mode ---------------------------------------------

    @mcp.tool(description=desc.CLUSTER_TAKES_BY_SILENCE)
    def cluster_takes_by_silence(
        transcript_path: str, silence_threshold_sec: float = 2.0
    ) -> ToolResponse:
        return _cluster_takes_by_silence(transcript_path, silence_threshold_sec)

    @mcp.tool(description=desc.ALIGN_TAKES_TO_SCRIPT)
    def align_takes_to_script(transcript_path: str, script_text: str) -> ToolResponse:
        return _align_takes_to_script(transcript_path, script_text)

    @mcp.tool(description=desc.GENERATE_FCPXML)
    def generate_fcpxml(sequence_spec: dict, output_path: str) -> ToolResponse:
        return _generate_fcpxml(sequence_spec, output_path)

    @mcp.tool(description=desc.EXTRACT_FRAME_GRID)
    def extract_frame_grid(
        video_path: str, num_frames: int = 16, overlay_timecodes: bool = True,
        cache_dir: str | None = None,
    ) -> Any:
        return _extract_frame_grid(video_path, num_frames, overlay_timecodes, cache_dir)

    @mcp.tool(description=desc.GET_CLIP_THUMBNAIL)
    def get_clip_thumbnail(video_path: str, timecode_sec: float) -> Any:
        return _get_clip_thumbnail(video_path, timecode_sec)

    # ----- Multicam / podcast mode ------------------------------------------

    @mcp.tool(description=desc.MC_DETECT_GROUPS)
    def detect_multicam_groups(folder: str) -> ToolResponse:
        return _detect_multicam_groups(folder)

    @mcp.tool(description=desc.MC_DIARIZE_SPEAKERS)
    def diarize_speakers(
        transcript_path: str, group_path: str, speaker_labels: list[str] | None = None
    ) -> ToolResponse:
        return _diarize_speakers(transcript_path, group_path, speaker_labels)

    @mcp.tool(description=desc.MC_PICK_ANGLE_PER_SEGMENT)
    def pick_angle_per_segment(
        transcript_path: str, diarization_path: str, group_path: str,
        reaction_interval_sec: float = 30.0, reaction_hold_sec: float = 2.0,
    ) -> ToolResponse:
        return _pick_angle_per_segment(
            transcript_path, diarization_path, group_path,
            reaction_interval_sec, reaction_hold_sec,
        )

    @mcp.tool(description=desc.MC_GENERATE_MULTICAM_FCPXML)
    def generate_multicam_fcpxml(
        angles_path: str, output_path: str,
        name: str = "roughcut-multicam", fps: float = 23.976,
        width: int = 1920, height: int = 1080,
    ) -> ToolResponse:
        return _generate_multicam_fcpxml(
            angles_path, output_path, name, fps, width, height,
        )

    # ----- Meta --------------------------------------------------------------

    @mcp.tool(description=desc.GET_PROJECT_PATHS)
    def get_project_paths() -> ToolResponse:
        return _get_project_paths()


# ---------------------------------------------------------------------------
# Implementations (each `_*` callable is unit-testable without FastMCP)
# ---------------------------------------------------------------------------


def _list_clips(folder: str, recursive: bool) -> ToolResponse:
    path = abs_dir(folder)
    if isinstance(path, ToolResponse):
        return path
    try:
        result = clips.list_clips(path, recursive=recursive)
    except Exception as e:  # noqa: BLE001
        log.exception("list_clips failed")
        return to_error(e)
    cdir = cache_dir(None)
    key = cache_io.stable_key("clips", str(path.resolve()), recursive)
    out = cache_io.write_json(cdir, "clip-lists", key, [c.model_dump(mode="json") for c in result])
    return ToolResponse(ok=True, summary={
        "clips_path": str(out),
        "clip_paths": [str(c.path) for c in result],
        "clip_count": len(result),
        "total_duration_sec": round(sum(c.duration_sec for c in result), 3),
        "folder": str(path),
    })


def _transcribe_video(
    video_path: str, language: str, model: str, cache_dir_override: str | None
) -> ToolResponse:
    path = abs_file(video_path)
    if isinstance(path, ToolResponse):
        return path
    lang = None if not language or language.lower() == "auto" else language
    cdir = cache_dir(cache_dir_override)
    try:
        t = transcribe.transcribe(path, cdir, model=model, language=lang)
    except Exception as e:  # noqa: BLE001
        log.exception("transcribe_video failed")
        return to_error(e)
    transcript_path = cdir / "transcripts" / transcribe._safe_model(model) / f"{transcribe.cache_key(path)}.json"
    full_text = " ".join(s.text.strip() for s in t.segments).strip()
    return ToolResponse(ok=True, summary={
        "transcript_path": str(transcript_path),
        "segment_count": len(t.segments),
        "duration_sec": round(t.duration, 3),
        "language": t.language,
        "first_200_chars": full_text[:200],
    })


def _cluster_takes_by_silence(transcript_path: str, threshold: float) -> ToolResponse:
    t = _load_transcript(transcript_path)
    if isinstance(t, ToolResponse):
        return t
    try:
        clusters_list = takes.cluster_by_silence(t, threshold)
    except Exception as e:  # noqa: BLE001
        log.exception("cluster_takes_by_silence failed")
        return to_error(e)
    cdir = cache_dir(None)
    key = cache_io.stable_key("clusters", transcript_path, threshold)
    out = cache_io.write_json(cdir, "clusters", key, [c.model_dump(mode="json") for c in clusters_list])
    longest = max((len(c.segment_indices) for c in clusters_list), default=0)
    return ToolResponse(ok=True, summary={
        "clusters_path": str(out),
        "cluster_count": len(clusters_list),
        "longest_cluster_segment_count": longest,
        "total_duration_sec": round(sum(c.out_sec - c.in_sec for c in clusters_list), 3),
    })


def _align_takes_to_script(transcript_path: str, script_text: str) -> ToolResponse:
    t = _load_transcript(transcript_path)
    if isinstance(t, ToolResponse):
        return t
    try:
        alignments_list = takes.align_to_script(t, script_text)
    except Exception as e:  # noqa: BLE001
        log.exception("align_takes_to_script failed")
        return to_error(e)
    cdir = cache_dir(None)
    key = cache_io.stable_key("alignments", transcript_path, hash(script_text))
    out = cache_io.write_json(cdir, "alignments", key, [a.model_dump(mode="json") for a in alignments_list])
    matched = sum(1 for a in alignments_list if a.candidate_segment_indices)
    return ToolResponse(ok=True, summary={
        "alignments_path": str(out),
        "line_count": len(alignments_list),
        "lines_with_match": matched,
        "lines_without_match": len(alignments_list) - matched,
    })


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
    return ToolResponse(ok=True, output_path=str(out), summary={
        "aroll_count": len(spec.aroll),
        "broll_count": len(spec.broll),
        "duration_sec": round(sum(s.out_sec - s.in_sec for s in spec.aroll), 3),
    })


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
        sheet_path = sheet_dir / f"{key}_{num_frames}_{int(overlay_timecodes)}.jpg"
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


def _detect_multicam_groups(folder: str) -> ToolResponse:
    path = abs_dir(folder)
    if isinstance(path, ToolResponse):
        return path
    try:
        groups = multicam.detect_groups(path)
    except Exception as e:  # noqa: BLE001
        log.exception("detect_multicam_groups failed")
        return to_error(e)
    cdir = cache_dir(None)
    folder_key = cache_io.stable_key("multicam-groups", str(path.resolve()))
    group_paths: list[str] = []
    for g in groups:
        gpath = cache_io.write_json(
            cdir, "multicam-groups", f"{folder_key}_{g.group_id}", g.model_dump(mode="json"),
        )
        group_paths.append(str(gpath))
    return ToolResponse(ok=True, summary={
        "group_count": len(groups),
        "groups": [
            {"group_id": g.group_id, "group_path": group_paths[i],
             "clip_count": len(g.clip_paths),
             "confidence": round(g.confidence, 3),
             "clip_paths": [str(p) for p in g.clip_paths],
             "offsets_sec": [round(o, 3) for o in g.offsets_sec]}
            for i, g in enumerate(groups)
        ],
    })


def _diarize_speakers(
    transcript_path: str, group_path: str, speaker_labels: list[str] | None
) -> ToolResponse:
    t = _load_transcript(transcript_path)
    if isinstance(t, ToolResponse):
        return t
    group = _load_group(group_path)
    if isinstance(group, ToolResponse):
        return group
    try:
        labels = multicam.diarize_by_mic_dominance(t, group, speaker_labels)
    except Exception as e:  # noqa: BLE001
        log.exception("diarize_speakers failed")
        return to_error(e)
    cdir = cache_dir(None)
    key = cache_io.stable_key("diarization", transcript_path, group.group_id)
    out = cache_io.write_json(cdir, "diarization", key, [l.model_dump(mode="json") for l in labels])
    counts: dict[str, int] = {}
    for l in labels:
        counts[l.speaker_label] = counts.get(l.speaker_label, 0) + 1
    return ToolResponse(ok=True, summary={
        "diarization_path": str(out),
        "segment_count": len(labels),
        "speaker_segment_counts": counts,
    })


def _pick_angle_per_segment(
    transcript_path: str, diarization_path: str, group_path: str,
    reaction_interval_sec: float, reaction_hold_sec: float,
) -> ToolResponse:
    t = _load_transcript(transcript_path)
    if isinstance(t, ToolResponse):
        return t
    group = _load_group(group_path)
    if isinstance(group, ToolResponse):
        return group
    try:
        raw = cache_io.read_json(Path(diarization_path))
        diarization = [SpeakerLabel.model_validate(d) for d in raw]
    except Exception as e:  # noqa: BLE001
        return ToolResponse(ok=False, error="invalid_spec",
                            message=f"diarization at {diarization_path} invalid: {e}")
    try:
        selections = multicam.pick_angles(
            t, diarization, group,
            reaction_interval_sec=reaction_interval_sec,
            reaction_hold_sec=reaction_hold_sec,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("pick_angle_per_segment failed")
        return to_error(e)
    cdir = cache_dir(None)
    key = cache_io.stable_key("angles", transcript_path, group.group_id, reaction_interval_sec)
    out = cache_io.write_json(cdir, "angles", key, [a.model_dump(mode="json") for a in selections])
    return ToolResponse(ok=True, summary={
        "angles_path": str(out),
        "angle_count": len(selections),
        "primary_count": sum(1 for a in selections if a.reason == "primary"),
        "reaction_count": sum(1 for a in selections if a.reason == "reaction"),
        "duration_sec": round(max((a.timeline_out_sec for a in selections), default=0.0), 3),
    })


def _generate_multicam_fcpxml(
    angles_path: str, output_path: str, name: str, fps: float, width: int, height: int,
) -> ToolResponse:
    out = abs_path(output_path)
    if isinstance(out, ToolResponse):
        return out
    try:
        raw = cache_io.read_json(Path(angles_path))
        selections = [AngleSelection.model_validate(a) for a in raw]
    except Exception as e:  # noqa: BLE001
        return ToolResponse(ok=False, error="invalid_spec",
                            message=f"angles at {angles_path} invalid: {e}")
    if not selections:
        return ToolResponse(ok=False, error="invalid_spec",
                            message="angle list is empty")
    try:
        fcpxml.write_multicam_fcpxml(
            selections, out, name=name, fps=fps, width=width, height=height,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("generate_multicam_fcpxml failed")
        return to_error(e)
    return ToolResponse(ok=True, output_path=str(out), summary={
        "angle_count": len(selections),
        "duration_sec": round(max(a.timeline_out_sec for a in selections), 3),
    })


def _get_project_paths() -> ToolResponse:
    return ToolResponse(ok=True, summary={
        "interview_folder": os.environ.get("ROUGHCUT_INTERVIEW_DIR") or None,
        "broll_folder": os.environ.get("ROUGHCUT_BROLL_DIR") or None,
        "script_path": os.environ.get("ROUGHCUT_SCRIPT_PATH") or None,
        "cache_dir": str(cache_dir(None)),
    })


# ---------------------------------------------------------------------------
# Internal loaders for path-based inputs
# ---------------------------------------------------------------------------


def _load_transcript(path: str) -> Transcript | ToolResponse:
    p = abs_file(path)
    if isinstance(p, ToolResponse):
        return p
    try:
        return Transcript.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return ToolResponse(ok=False, error="invalid_spec",
                            message=f"transcript at {path} invalid: {e}")


def _load_group(path: str) -> MulticamGroup | ToolResponse:
    p = abs_file(path)
    if isinstance(p, ToolResponse):
        return p
    try:
        return MulticamGroup.model_validate(cache_io.read_json(p))
    except Exception as e:  # noqa: BLE001
        return ToolResponse(ok=False, error="invalid_spec",
                            message=f"group at {path} invalid: {e}")
