"""MCP tool registrations + implementations.

Three rules the agent should keep in mind:

1. **Size-bounded returns.** Real podcast transcripts and per-line
   script alignments exceed Claude Desktop's 1 MB tool-result cap.
   Tools that can grow large write their full payload to disk under
   `cache_dir` and return a small `data` dict with a `*_path` field.

2. **Long-running work is async.** Transcription, clustering, multicam
   sync, diarization, alignment, and angle-picking all return
   immediately with a `job_id`. Poll `check_job_status(job_id)` until
   `status` is `succeeded`. `list_jobs` shows recent activity so a
   fresh chat session can pick up where an old one left off.

3. **Mode separation.** Doc / interview tools and multicam / podcast
   tools share `transcribe_video` but otherwise live in separate
   workflows. Don't mix them in one rough cut.

Response shape (v0.6.5+):
- `data`: the rich result dict (was called `summary` pre-v0.6.5; both
  fields are populated for back-compat).
- `next_steps`: optional dict pointing at obvious downstream calls.
- `error` / `message`: only on failure.
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

from roughcut_core import (
    broll, cache_io, clips, documentary, edl, fcp7_xml, fcpxml,
    fcpxml_validate, jobs, models_catalog, system_status, transcribe,
)
from roughcut_core.models import AngleSelection, SequenceSpec
from roughcut_mcp import descriptions as desc
from roughcut_mcp import log_tail
from roughcut_mcp.responses import (
    ToolResponse, abs_dir, abs_file, abs_path, cache_dir, to_error,
)

log = logging.getLogger("roughcut_mcp.tools")


def register_tools(mcp: FastMCP) -> None:
    """Register every roughcut tool on the given FastMCP server."""

    # ----- Synchronous tools (fast, return inline) ---------------------------

    @mcp.tool(description=desc.LIST_CLIPS)
    def list_clips(folder: str, recursive: bool = True) -> ToolResponse:
        return _list_clips(folder, recursive)

    @mcp.tool(description=desc.GENERATE_FCPXML)
    def generate_fcpxml(sequence_spec: dict, output_path: str) -> ToolResponse:
        return _generate_fcpxml(sequence_spec, output_path)

    @mcp.tool(description=desc.VALIDATE_FCPXML)
    def validate_fcpxml(fcpxml_path: str) -> ToolResponse:
        return _validate_fcpxml(fcpxml_path)

    @mcp.tool(description=desc.GENERATE_MULTICAM_FCPXML)
    def generate_multicam_fcpxml(
        angles_path: str, output_path: str,
        name: str = "roughcut-multicam", fps: float = 23.976,
        width: int = 1920, height: int = 1080,
    ) -> ToolResponse:
        return _generate_multicam_fcpxml(angles_path, output_path, name, fps, width, height)

    @mcp.tool(description=desc.EXTRACT_FRAME_GRID)
    def extract_frame_grid(
        video_path: str, num_frames: int = 9, overlay_timecodes: bool = True,
        tile_size: int = broll.DEFAULT_TILE_SIZE,
        jpeg_quality: int = broll.DEFAULT_JPEG_QUALITY,
    ) -> Any:
        return _extract_frame_grid(
            video_path, num_frames, overlay_timecodes, tile_size, jpeg_quality,
        )

    @mcp.tool(description=desc.GET_CLIP_THUMBNAIL)
    def get_clip_thumbnail(video_path: str, timecode_sec: float) -> Any:
        return _get_clip_thumbnail(video_path, timecode_sec)

    @mcp.tool(description=desc.GET_PROJECT_PATHS)
    def get_project_paths() -> ToolResponse:
        return _get_project_paths()

    @mcp.tool(description=desc.GET_SYSTEM_STATUS)
    def get_system_status() -> ToolResponse:
        return _get_system_status()

    @mcp.tool(description=desc.GET_SERVER_LOGS)
    def get_server_logs(tail: int = 100) -> ToolResponse:
        return _get_server_logs(tail)

    # ----- Documentary / unscripted mode (sync; reads cached JSON) ---------

    @mcp.tool(description=desc.READ_TRANSCRIPT)
    def read_transcript(
        transcript_path: str, start_segment: int = 0,
        end_segment: int | None = None, max_chars: int = 900_000,
    ) -> ToolResponse:
        return _read_transcript(transcript_path, start_segment, end_segment, max_chars)

    @mcp.tool(description=desc.SEARCH_TRANSCRIPTS)
    def search_transcripts(
        query: str, folder_path: str | None = None,
        max_results: int = 20, context_segments: int = 2,
    ) -> ToolResponse:
        return _search_transcripts(query, folder_path, max_results, context_segments)

    @mcp.tool(description=desc.SUMMARIZE_CLIP)
    def summarize_clip(transcript_path: str) -> ToolResponse:
        return _summarize_clip(transcript_path)

    @mcp.tool(description=desc.LOOKUP_TRANSCRIPT_BY_VIDEO_PATH)
    def lookup_transcript_by_video_path(video_path: str) -> ToolResponse:
        return _lookup_transcript_by_video_path(video_path)

    # ----- Async-job tools (return job_id, work in subprocess) ---------------

    @mcp.tool(description=desc.TRANSCRIBE_VIDEO)
    def transcribe_video(
        video_path: str, language: str = "en",
        model: str = models_catalog.DEFAULT_MODEL,
    ) -> ToolResponse:
        path = abs_file(video_path)
        if isinstance(path, ToolResponse):
            return path
        return _spawn("transcribe_video", {
            "video_path": str(path), "language": language, "model": model,
        })

    @mcp.tool(description=desc.PREWARM_MODEL)
    def prewarm_model(model_name: str = "large-v3") -> ToolResponse:
        if model_name not in models_catalog.SHORT_TO_HF:
            return ToolResponse(
                ok=False, error="invalid_spec",
                message=(f"unknown model '{model_name}'. "
                         f"Choices: {list(models_catalog.SHORT_TO_HF)}"),
            )
        return _spawn("prewarm_model", {"model_name": model_name})

    @mcp.tool(description=desc.CLUSTER_TAKES_BY_SILENCE)
    def cluster_takes_by_silence(
        transcript_path: str, silence_threshold_sec: float = 2.0,
    ) -> ToolResponse:
        p = abs_file(transcript_path)
        if isinstance(p, ToolResponse):
            return p
        return _spawn("cluster_takes_by_silence", {
            "transcript_path": str(p), "silence_threshold_sec": silence_threshold_sec,
        })

    @mcp.tool(description=desc.ALIGN_TAKES_TO_SCRIPT)
    def align_takes_to_script(transcript_path: str, script_text: str) -> ToolResponse:
        p = abs_file(transcript_path)
        if isinstance(p, ToolResponse):
            return p
        return _spawn("align_takes_to_script", {
            "transcript_path": str(p), "script_text": script_text,
        })

    @mcp.tool(description=desc.MC_DETECT_GROUPS)
    def detect_multicam_groups(folder: str) -> ToolResponse:
        p = abs_dir(folder)
        if isinstance(p, ToolResponse):
            return p
        return _spawn("detect_multicam_groups", {"folder": str(p)})

    @mcp.tool(description=desc.MC_DIARIZE_SPEAKERS)
    def diarize_speakers(
        transcript_path: str, group_path: str,
        speaker_labels: list[str] | None = None,
    ) -> ToolResponse:
        tp = abs_file(transcript_path)
        if isinstance(tp, ToolResponse):
            return tp
        gp = abs_file(group_path)
        if isinstance(gp, ToolResponse):
            return gp
        return _spawn("diarize_speakers", {
            "transcript_path": str(tp), "group_path": str(gp),
            "speaker_labels": speaker_labels,
        })

    @mcp.tool(description=desc.MC_PICK_ANGLE_PER_SEGMENT)
    def pick_angle_per_segment(
        transcript_path: str, diarization_path: str, group_path: str,
        reaction_interval_sec: float = 30.0, reaction_hold_sec: float = 2.0,
    ) -> ToolResponse:
        for name in ("transcript_path", "diarization_path", "group_path"):
            p = abs_file({"transcript_path": transcript_path,
                          "diarization_path": diarization_path,
                          "group_path": group_path}[name])
            if isinstance(p, ToolResponse):
                return p
        return _spawn("pick_angle_per_segment", {
            "transcript_path": str(Path(transcript_path).resolve()),
            "diarization_path": str(Path(diarization_path).resolve()),
            "group_path": str(Path(group_path).resolve()),
            "reaction_interval_sec": reaction_interval_sec,
            "reaction_hold_sec": reaction_hold_sec,
        })

    # ----- Job management ----------------------------------------------------

    @mcp.tool(description=desc.CHECK_JOB_STATUS)
    def check_job_status(job_id: str) -> ToolResponse:
        return _check_job_status(job_id)

    @mcp.tool(description=desc.LIST_JOBS)
    def list_jobs(
        status: str | None = None, limit: int = 100, offset: int = 0,
    ) -> ToolResponse:
        return _list_jobs(status, limit, offset)

    @mcp.tool(description=desc.CANCEL_JOB)
    def cancel_job(job_id: str) -> ToolResponse:
        return _cancel_job(job_id)

    @mcp.tool(description=desc.RESUME_JOB)
    def resume_job(job_id: str) -> ToolResponse:
        return _resume_job(job_id)

    @mcp.tool(description=desc.RESTART_WORKERS)
    def restart_workers() -> ToolResponse:
        return _restart_workers()


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


def _spawn(tool_name: str, args: dict) -> ToolResponse:
    """Spawn a worker subprocess (or return cached `succeeded` job).

    Returns a small ToolResponse pointing at the job_id. The agent
    polls `check_job_status(job_id)` from here. v0.6.5 also includes
    `queue_position` and worker-pool state so the agent can predict
    whether it'll start immediately or sit behind others.
    """
    cdir = cache_dir(None)
    try:
        job = jobs.spawn(cdir, tool_name, args)
    except Exception as e:  # noqa: BLE001
        log.exception("job spawn failed")
        return to_error(e)
    data: dict = {
        "job_id": job.job_id, "status": job.status, "tool_name": job.tool_name,
        "started_at": job.started_at,
        "poll_with": f"check_job_status('{job.job_id}')",
    }
    if job.status == "succeeded":
        data["result_path"] = job.result_path
        data["result_summary"] = job.result_summary
        data["note"] = "cached — identical inputs; no work was done"
    else:
        pos = jobs.queue_position(cdir, job.job_id)
        if pos is not None:
            data["queue_position"] = pos
        data["pool_size"] = jobs.pool_size()
        data["live_workers"] = len(jobs.live_worker_pids(cdir))
    next_steps = {"poll": f"check_job_status('{job.job_id}')"}
    return ToolResponse(ok=True, data=data, next_steps=next_steps)


def _check_job_status(job_id: str) -> ToolResponse:
    cdir = cache_dir(None)
    job = jobs.read(cdir, job_id)
    if job is None:
        return ToolResponse(ok=False, error="not_a_file",
                            message=f"no job with id {job_id}",
                            data={"hint": "Use list_jobs to see recent jobs."})
    # If status claims `running` but pid is dead, reflect reality.
    if job.status in ("started", "running") and (not job.pid or not jobs._alive(job.pid)):
        job.status = "interrupted"
        job.current_step = "interrupted (worker process died)"
        jobs.write(cdir, job)
    return ToolResponse(ok=True, data=job.model_dump(mode="json"))


def _list_jobs(status: str | None, limit: int, offset: int) -> ToolResponse:
    cdir = cache_dir(None)
    js = jobs.list_jobs(cdir, status=status)  # type: ignore[arg-type]
    capped = max(1, min(1000, int(limit or 100)))
    start = max(0, int(offset or 0))
    page = js[start : start + capped]
    data = {
        "total_count": len(js),
        "returned_count": len(page),
        "offset": start,
        "limit": capped,
        "next_offset": (start + len(page)) if (start + len(page)) < len(js) else None,
        "jobs": [
            {"job_id": j.job_id, "tool_name": j.tool_name, "status": j.status,
             "started_at": j.started_at, "current_step": j.current_step,
             "progress_pct": j.progress_pct, "result_path": j.result_path,
             "error": j.error}
            for j in page
        ],
    }
    return ToolResponse(ok=True, data=data)


def _restart_workers() -> ToolResponse:
    cdir = cache_dir(None)
    try:
        result = jobs.restart_workers(cdir)
    except Exception as e:  # noqa: BLE001
        log.exception("restart_workers failed")
        return to_error(e)
    return ToolResponse(ok=True, data=result)


def _cancel_job(job_id: str) -> ToolResponse:
    cdir = cache_dir(None)
    try:
        job = jobs.cancel(cdir, job_id)
    except FileNotFoundError:
        return ToolResponse(ok=False, error="not_a_file",
                            message=f"no job with id {job_id}")
    return ToolResponse(ok=True, data={
        "job_id": job.job_id, "status": job.status,
    })


def _resume_job(job_id: str) -> ToolResponse:
    """Re-run a failed / interrupted / cancelled job from scratch.

    Per-step caches (extracted WAV, downloaded model) survive across
    runs, so resuming is much cheaper than the first attempt.
    """
    cdir = cache_dir(None)
    job = jobs.read(cdir, job_id)
    if job is None:
        return ToolResponse(ok=False, error="not_a_file",
                            message=f"no job with id {job_id}")
    if job.status == "succeeded":
        return ToolResponse(ok=True, data={
            "job_id": job.job_id, "status": "succeeded",
            "note": "already succeeded; nothing to resume",
            "result_path": job.result_path,
            "result_summary": job.result_summary,
        })
    if job.status == "running" and job.pid and jobs._alive(job.pid):
        return ToolResponse(ok=False, error="invalid_spec",
                            message=f"job {job_id} is still running; cancel it first")
    # Reset and re-spawn with identical args.
    fresh = jobs.spawn(cdir, job.tool_name, job.args)
    return ToolResponse(ok=True, data={
        "job_id": fresh.job_id, "status": fresh.status,
        "poll_with": f"check_job_status('{fresh.job_id}')",
        "note": "resumed from cached intermediate state where possible",
    })


# ----- Synchronous tool implementations -------------------------------------


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
    out = cache_io.write_json(cdir, "clip-lists", key,
                              [c.model_dump(mode="json") for c in result])
    return ToolResponse(ok=True, data={
        "clips_path": str(out),
        "clip_paths": [str(c.path) for c in result],
        "clip_count": len(result),
        "total_duration_sec": round(sum(c.duration_sec for c in result), 3),
        "folder": str(path),
    }, next_steps={
        "transcribe_one": (
            "transcribe_video(video_path='<absolute path from clip_paths>')"
        ),
        "preview_grid": (
            "extract_frame_grid(video_path='<abs>', num_frames=9) "
            "— good for b-roll triage"
        ),
    })


def _generate_fcpxml(sequence_spec_data: dict, output_path: str) -> ToolResponse:
    """Emit three timeline formats + self-validate before declaring success.

    Writes:
      - `<basename>.fcpxml` (Final Cut, FCPXML 1.10)
      - `<basename>.xml`    (Premiere, FCP 7 XMEML v5)
      - `<basename>.edl`    (universal V1 fallback)
      - `<basename>.relink.csv` (EDL reel → source mapping)

    Internally runs `fcpxml_validate.validate_fcpxml` after writing the
    .fcpxml. If validation fails the response is `ok=False` with the
    error list — we don't ship invalid output silently.
    """
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
                            message="SequenceSpec.aroll is empty")
    fcpxml_path = out.with_suffix(".fcpxml")
    fcp7_path = out.with_suffix(".xml")
    edl_path = out.with_suffix(".edl")
    try:
        fcpxml.write_fcpxml(spec, fcpxml_path)
        fcp7_xml.write_fcp7_xml(spec, fcp7_path)
        edl_meta = edl.write_edl(spec, edl_path)
    except Exception as e:  # noqa: BLE001
        log.exception("generate_fcpxml failed")
        return to_error(e)

    # v0.6.5 P3 #18: self-validate the FCPXML before declaring success.
    # If the dedup-by-resolved-path fix breaks for some unanticipated input,
    # the agent gets a clear error here instead of an opaque FCP rejection.
    validation = fcpxml_validate.validate_fcpxml(fcpxml_path)
    if not validation["ok"]:
        return ToolResponse(
            ok=False, error="invalid_spec",
            message=("generated FCPXML failed self-validation: "
                     + "; ".join(validation["errors"])),
            data={
                "fcpxml_path": str(fcpxml_path),
                "validation": validation,
            },
        )

    return ToolResponse(ok=True, output_path=str(fcpxml_path), data={
        "fcpxml_path": str(fcpxml_path),
        "fcp7_xml_path": str(fcp7_path),
        "edl_path": str(edl_path),
        "relink_csv_path": edl_meta["relink_csv_path"],
        "edl_note": edl_meta["edl_note"],
        "aroll_count": len(spec.aroll), "broll_count": len(spec.broll),
        "duration_sec": round(
            sum(s.source_out_sec - s.source_in_sec for s in spec.aroll), 3,
        ),
        "validation": validation,
        "import_hints": {
            "final_cut_pro": str(fcpxml_path),
            "premiere_pro": str(fcp7_path),
            "fallback_any_nle": str(edl_path),
        },
    }, next_steps={
        "verify": f"validate_fcpxml(fcpxml_path='{fcpxml_path}')",
        "open_in_finder": (
            f"The .fcpxml/.xml/.edl/.relink.csv are siblings at "
            f"'{out.parent}'."
        ),
    })


def _validate_fcpxml(fcpxml_path: str) -> ToolResponse:
    p = abs_file(fcpxml_path)
    if isinstance(p, ToolResponse):
        return p
    try:
        result = fcpxml_validate.validate_fcpxml(p)
    except Exception as e:  # noqa: BLE001
        log.exception("validate_fcpxml failed")
        return to_error(e)
    return ToolResponse(
        ok=bool(result["ok"]),
        error=None if result["ok"] else "invalid_spec",
        message=(None if result["ok"]
                 else "; ".join(result["errors"])),
        data=result,
    )


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
        return ToolResponse(ok=False, error="invalid_spec", message="angle list is empty")
    try:
        fcpxml.write_multicam_fcpxml(
            selections, out, name=name, fps=fps, width=width, height=height,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("generate_multicam_fcpxml failed")
        return to_error(e)
    return ToolResponse(ok=True, output_path=str(out), data={
        "angle_count": len(selections),
        "duration_sec": round(max(a.timeline_out_sec for a in selections), 3),
    })


def _extract_frame_grid(
    video_path: str, num_frames: int, overlay_timecodes: bool,
    tile_size: int = broll.DEFAULT_TILE_SIZE,
    jpeg_quality: int = broll.DEFAULT_JPEG_QUALITY,
) -> Any:
    path = abs_file(video_path)
    if isinstance(path, ToolResponse):
        return path
    try:
        meta = clips.probe_clip(path)
        sheet_dir = cache_dir(None) / "broll-grids"
        key = transcribe.cache_key(path)
        sheet_path = sheet_dir / (
            f"{key}_{num_frames}_{int(overlay_timecodes)}_"
            f"{int(tile_size)}_{int(jpeg_quality)}.jpg"
        )
        if not sheet_path.exists():
            broll.build_contact_sheet(
                path, sheet_path, duration=meta.duration_sec,
                overlay_timecodes=overlay_timecodes, num_frames=num_frames,
                tile_size=tile_size, jpeg_quality=jpeg_quality,
            )
    except Exception as e:  # noqa: BLE001
        log.exception("extract_frame_grid failed")
        return to_error(e)
    sidecar = json.dumps({"image_path": str(sheet_path),
                          "duration_sec": meta.duration_sec,
                          "num_frames": num_frames,
                          "tile_size": tile_size,
                          "jpeg_quality": jpeg_quality})
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
    return ToolResponse(ok=True, data={
        "interview_folder": os.environ.get("ROUGHCUT_INTERVIEW_DIR") or None,
        "broll_folder": os.environ.get("ROUGHCUT_BROLL_DIR") or None,
        "script_path": os.environ.get("ROUGHCUT_SCRIPT_PATH") or None,
        "cache_dir": str(cache_dir(None)),
    })


def _get_system_status() -> ToolResponse:
    cdir = cache_dir(None)
    status = system_status.collect(cdir)
    problems = [k for k, v in status.items() if isinstance(v, dict) and v.get("ok") is False]
    return ToolResponse(ok=not problems, data={
        "all_ok": not problems,
        "problems": problems,
        "details": status,
    })


def _get_server_logs(tail: int) -> ToolResponse:
    """Self-diagnosis: aggregate watchdog.log + worker-N.log into one tail."""
    try:
        capped_tail = max(1, min(1000, int(tail or 100)))
        result = log_tail.tail_recent(cache_dir(None), capped_tail)
    except Exception as e:  # noqa: BLE001
        log.exception("get_server_logs failed")
        return to_error(e)
    return ToolResponse(ok=True, data=result)


def _read_transcript(
    transcript_path: str, start_segment: int, end_segment: int | None, max_chars: int,
) -> ToolResponse:
    p = abs_file(transcript_path)
    if isinstance(p, ToolResponse):
        return p
    try:
        result = documentary.read_transcript(p, start_segment, end_segment, max_chars)
    except Exception as e:  # noqa: BLE001
        log.exception("read_transcript failed")
        return to_error(e)
    next_steps: dict = {}
    if result.get("has_more"):
        next_steps["read_more"] = (
            f"read_transcript(transcript_path='{transcript_path}', "
            f"start_segment={result['next_start']})"
        )
    return ToolResponse(ok=True, data=result, next_steps=next_steps or None)


def _search_transcripts(
    query: str, folder_path: str | None, max_results: int, context_segments: int,
) -> ToolResponse:
    folder: Path | None = None
    if folder_path:
        folder_resolved = abs_dir(folder_path)
        if isinstance(folder_resolved, ToolResponse):
            return folder_resolved
        folder = folder_resolved
    try:
        result = documentary.search_transcripts(
            query, cache_dir(None), folder, max_results, context_segments,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("search_transcripts failed")
        return to_error(e)
    return ToolResponse(ok=True, data=result)


def _summarize_clip(transcript_path: str) -> ToolResponse:
    p = abs_file(transcript_path)
    if isinstance(p, ToolResponse):
        return p
    try:
        result = documentary.summarize_clip(p)
    except Exception as e:  # noqa: BLE001
        log.exception("summarize_clip failed")
        return to_error(e)
    return ToolResponse(ok=True, data=result, next_steps={
        "read_full": f"read_transcript(transcript_path='{transcript_path}')",
    })


def _lookup_transcript_by_video_path(video_path: str) -> ToolResponse:
    """Find the cached transcript that goes with a given source video."""
    p = abs_file(video_path)
    if isinstance(p, ToolResponse):
        return p
    try:
        result = documentary.lookup_transcript_by_video_path(p, cache_dir(None))
    except Exception as e:  # noqa: BLE001
        log.exception("lookup_transcript_by_video_path failed")
        return to_error(e)
    next_steps: dict | None = None
    if result.get("found"):
        tp = result["best_match"]["transcript_path"]
        next_steps = {
            "read_full": f"read_transcript(transcript_path='{tp}')",
            "summarize": f"summarize_clip(transcript_path='{tp}')",
        }
    else:
        next_steps = {
            "transcribe_first": (
                f"transcribe_video(video_path='{p}')"
            ),
        }
    return ToolResponse(ok=bool(result.get("found")), data=result,
                        next_steps=next_steps)
