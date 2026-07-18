"""Job worker subprocess.

v0.6.3 redesign. Spawned by `roughcut_core.jobs._spawn_worker()` (no
argv): the worker pops job_ids off the on-disk queue and runs them
serially. Survives Claude Desktop quit (`start_new_session=True`).
Exits after `IDLE_EXIT_SECONDS` of no work — the next `jobs.spawn()`
call respawns it via `_ensure_workers`.

Heavy imports (mlx-whisper, scipy) live inside the dispatch functions
so the worker module itself stays importable on Linux for tests.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import traceback as tb
from pathlib import Path
from typing import Any

from roughcut_core import jobs

# After this many seconds with no jobs queued, the worker shuts down.
# Re-spawn is cheap; sitting idle wastes a python process slot.
#
# v0.6.5 bumped this from 120 to 900: editorial sessions tend to have
# minutes-long pauses between transcribes (the user reads the result,
# decides what to do next). At 120s the worker exits during the pause,
# and the next transcribe takes a cold-start hit re-spawning the
# subprocess and re-loading mlx-whisper from disk. 15 minutes is long
# enough to cover a thinking pause; long enough to be perceived as
# "instant" by humans; short enough to free RAM if the user genuinely
# moves on.
IDLE_EXIT_SECONDS = float(os.environ.get("ROUGHCUT_WORKER_IDLE_SECONDS", "900"))

# Active job id while a handler is running. SIGTERM uses this to flip
# the currently-executing job to "cancelled" before the worker exits.
_current_job_id: str | None = None
_current_cache_dir: Path | None = None


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    global _current_cache_dir
    cache_dir = Path(os.environ.get("ROUGHCUT_CACHE_DIR") or (Path.home() / ".cache" / "roughcut"))
    _current_cache_dir = cache_dir
    slot = int(os.environ.get("ROUGHCUT_WORKER_SLOT", "0"))
    pid_file = jobs._worker_pid_path(cache_dir, slot)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    signal.signal(signal.SIGTERM, _on_term)

    try:
        _run_loop(cache_dir)
    finally:
        try:
            if pid_file.read_text().strip() == str(os.getpid()):
                pid_file.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    return 0


def _run_loop(cache_dir: Path) -> None:
    global _current_job_id
    idle_since: float | None = None
    while True:
        job_id = jobs.pop_from_queue(cache_dir)
        if job_id is None:
            if idle_since is None:
                idle_since = time.time()
            elif time.time() - idle_since > IDLE_EXIT_SECONDS:
                return
            time.sleep(0.5)
            continue
        idle_since = None
        _current_job_id = job_id
        try:
            _run_one(cache_dir, job_id)
        finally:
            _current_job_id = None


def _on_term(_sig: int, _frame: Any) -> None:
    """SIGTERM lands here. Flip the active job (if any) to cancelled, then exit."""
    if _current_job_id and _current_cache_dir is not None:
        j = jobs.read(_current_cache_dir, _current_job_id)
        if j and j.status in ("queued", "started", "running"):
            j.status = "cancelled"
            j.current_step = "cancelled by signal"
            jobs.write(_current_cache_dir, j)
    sys.exit(130)


def _run_one(cache_dir: Path, job_id: str) -> None:
    job = jobs.read(cache_dir, job_id)
    if job is None:
        return
    # Honor pre-pop cancellation: cancel() may have flipped the record
    # to "cancelled" while this id was sitting in the queue.
    if job.status == "cancelled":
        return
    job.status = "running"
    job.pid = os.getpid()
    job.current_step = "starting"
    jobs.write(cache_dir, job)

    try:
        result_path, summary = _dispatch(job, cache_dir)
        job.status = "succeeded"
        job.progress_pct = 100.0
        job.current_step = "done"
        job.result_path = str(result_path) if result_path else None
        job.result_summary = summary
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.traceback = tb.format_exc()
        job.hint = _hint_for(exc)
    jobs.write(cache_dir, job)


# ---------------------------------------------------------------------------
# Tool-specific dispatchers
# ---------------------------------------------------------------------------


def _dispatch(job: jobs.Job, cache_dir: Path) -> tuple[Path | None, dict]:
    handler = _HANDLERS.get(job.tool_name)
    if handler is None:
        raise ValueError(f"worker: no handler for tool '{job.tool_name}'")
    return handler(job, cache_dir)


def _do_transcribe_video(job: jobs.Job, cache_dir: Path) -> tuple[Path, dict]:
    from roughcut_core import models_catalog, transcribe
    args = job.args
    path = Path(args["video_path"]).resolve()
    requested_model = args.get("model") or models_catalog.DEFAULT_MODEL
    lang = args.get("language")
    # v0.6.5 P3 #16: Whisper aggressively hallucinates languages on
    # near-silent / low-speech clips (DG4A1666.MP4 was tagged Welsh in
    # real shoots). Default to English unless the caller asks for auto.
    # Pass "auto" explicitly to detect; pass an ISO code to force.
    if lang is None or lang == "":
        lang = "en"
    elif lang.lower() == "auto":
        lang = None

    # Auto-prewarm: if the requested model isn't on disk yet, fetch it
    # in-flight so the user never has to think about a separate download.
    if requested_model in models_catalog.SHORT_TO_HF and not models_catalog.is_cached(requested_model):
        size = models_catalog.APPROX_SIZE_MB.get(requested_model, 0)
        _step(job, cache_dir, f"downloading whisper-{requested_model} (~{size} MB)", progress=5.0)
        _download_hf_model(models_catalog.SHORT_TO_HF[requested_model])

    model_arg = models_catalog.resolve(requested_model)
    _step(job, cache_dir, "extracting audio + transcribing", progress=30.0)
    t = transcribe.transcribe(path, cache_dir, model=model_arg, language=lang)
    # Cache key uses the short name so the on-disk transcript path is
    # stable across "small" vs "/abs/path/to/small" resolutions.
    transcript_path = cache_dir / "transcripts" / transcribe._safe_model(requested_model) / f"{transcribe.cache_key(path)}.json"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    if not transcript_path.exists():
        transcript_path.write_text(t.model_dump_json(), encoding="utf-8")
    full_text = " ".join(s.text.strip() for s in t.segments).strip()

    # v0.6.5 P3 #16: detect the "Whisper saw silence and hallucinated"
    # case. Heuristic: less than 5 segments AND less than 50 chars of
    # speech text on a clip longer than 10 seconds. Don't blow up —
    # the agent might still want to know — but flag it clearly so the
    # cut-builder doesn't try to source bites from a clip that has none.
    low_speech = (
        t.duration > 10.0
        and len(t.segments) < 5
        and len(full_text) < 50
    )

    summary = {
        "transcript_path": str(transcript_path),
        "source_video": str(path),
        "segment_count": len(t.segments),
        "duration_sec": round(t.duration, 3),
        "language": t.language,
        "language_requested": lang or "auto",
        "model": requested_model,
        "low_speech_content": low_speech,
        "first_200_chars": full_text[:200],
        # Discoverability: chat-side Claude shouldn't have to stumble onto
        # the docs-mode tools by accident. Surface them in every transcribe
        # result so a fresh agent knows the obvious next moves.
        "next_tools": [
            {"name": "summarize_clip",
             "what": "Deterministic snippet view (no LLM) — opening/closing 200 chars + longest segment."},
            {"name": "read_transcript",
             "what": "Page through the full transcript in ~900 KB chunks (use start_segment/next_start)."},
            {"name": "search_transcripts",
             "what": "Case-insensitive substring search across every cached transcript."},
        ],
    }
    if low_speech:
        summary["hint"] = (
            f"Clip is {t.duration:.1f}s but only {len(t.segments)} segments / "
            f"{len(full_text)} chars of speech were detected. Likely b-roll, "
            "ambience, or near-silence. Don't try to source dialogue from this clip."
        )
    return transcript_path, summary


def _do_prewarm_model(job: jobs.Job, cache_dir: Path) -> tuple[None, dict]:
    """Download a whisper model in the background so a later transcribe is instant."""
    from roughcut_core import models_catalog
    name = job.args["model_name"]
    if name not in models_catalog.SHORT_TO_HF:
        raise ValueError(
            f"unknown model '{name}'. Choices: {list(models_catalog.SHORT_TO_HF)}"
        )
    if models_catalog.is_cached(name):
        return None, {"model_name": name, "already_cached": True}
    repo = models_catalog.SHORT_TO_HF[name]
    size = models_catalog.APPROX_SIZE_MB.get(name, 0)
    _step(job, cache_dir, f"downloading {repo} (~{size} MB)", progress=5.0)
    _download_hf_model(repo)
    return None, {"model_name": name, "hf_repo": repo, "downloaded": True}


def _download_hf_model(repo_id: str) -> None:
    """Pull the full repo snapshot into the HF cache. Lazy import."""
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=repo_id)


def _do_cluster(job: jobs.Job, cache_dir: Path) -> tuple[Path, dict]:
    from roughcut_core import cache_io, takes
    from roughcut_core.models import Transcript
    transcript_path = Path(job.args["transcript_path"])
    threshold = float(job.args.get("silence_threshold_sec", 2.0))
    _step(job, cache_dir, "loading transcript", progress=20.0)
    t = Transcript.model_validate_json(transcript_path.read_text(encoding="utf-8"))
    _step(job, cache_dir, "clustering", progress=60.0)
    clusters = takes.cluster_by_silence(t, threshold)
    key = cache_io.stable_key("clusters", str(transcript_path), threshold)
    out = cache_io.write_json(cache_dir, "clusters", key, [c.model_dump(mode="json") for c in clusters])
    longest_seg_count = max((len(c.segment_indices) for c in clusters), default=0)
    longest_sec = max(
        ((c.out_sec - c.in_sec) for c in clusters), default=0.0,
    )
    # v0.6.5 P2 #13: inline a few cluster previews so the agent doesn't
    # have to read the whole JSON file to decide which cluster looks
    # interesting. Keeps preview cheap (~3 clusters, text trimmed).
    sorted_by_len = sorted(
        clusters, key=lambda c: -(c.out_sec - c.in_sec),
    )
    preview = [
        {
            "cluster_id": c.cluster_id,
            "start_sec": round(c.in_sec, 3),
            "end_sec": round(c.out_sec, 3),
            "duration_sec": round(c.out_sec - c.in_sec, 3),
            "segment_count": len(c.segment_indices),
            "text_excerpt": (c.text[:140] + "…") if len(c.text) > 140 else c.text,
        }
        for c in sorted_by_len[:3]
    ]
    summary = {
        "clusters_path": str(out),
        "cluster_count": len(clusters),
        "longest_cluster_segment_count": longest_seg_count,
        "longest_cluster_sec": round(longest_sec, 3),
        "total_duration_sec": round(sum(c.out_sec - c.in_sec for c in clusters), 3),
        "preview": preview,
    }
    return out, summary


def _do_align(job: jobs.Job, cache_dir: Path) -> tuple[Path, dict]:
    from roughcut_core import cache_io, takes
    from roughcut_core.models import Transcript
    transcript_path = Path(job.args["transcript_path"])
    script_text = job.args["script_text"]
    _step(job, cache_dir, "loading transcript", progress=20.0)
    t = Transcript.model_validate_json(transcript_path.read_text(encoding="utf-8"))
    _step(job, cache_dir, "fuzzy-matching segments to script lines", progress=60.0)
    alignments = takes.align_to_script(t, script_text)
    key = cache_io.stable_key("alignments", str(transcript_path), hash(script_text))
    out = cache_io.write_json(cache_dir, "alignments", key, [a.model_dump(mode="json") for a in alignments])
    matched = sum(1 for a in alignments if a.candidate_segment_indices)
    summary = {
        "alignments_path": str(out), "line_count": len(alignments),
        "lines_with_match": matched, "lines_without_match": len(alignments) - matched,
    }
    return out, summary


def _do_detect_multicam(job: jobs.Job, cache_dir: Path) -> tuple[Path | None, dict]:
    from roughcut_core import cache_io, multicam
    folder = Path(job.args["folder"])
    _step(job, cache_dir, "cross-correlating audio across clips", progress=30.0)
    groups = multicam.detect_groups(folder)
    folder_key = cache_io.stable_key("multicam-groups", str(folder.resolve()))
    group_paths: list[str] = []
    for g in groups:
        gpath = cache_io.write_json(
            cache_dir, "multicam-groups", f"{folder_key}_{g.group_id}", g.model_dump(mode="json"),
        )
        group_paths.append(str(gpath))
    summary = {
        "group_count": len(groups),
        "groups": [
            {"group_id": g.group_id, "group_path": group_paths[i],
             "clip_count": len(g.clip_paths), "confidence": round(g.confidence, 3),
             "clip_paths": [str(p) for p in g.clip_paths],
             "offsets_sec": [round(o, 3) for o in g.offsets_sec]}
            for i, g in enumerate(groups)
        ],
    }
    return None, summary


def _do_diarize(job: jobs.Job, cache_dir: Path) -> tuple[Path, dict]:
    from roughcut_core import cache_io, multicam
    from roughcut_core.models import MulticamGroup, Transcript
    transcript_path = Path(job.args["transcript_path"])
    group_path = Path(job.args["group_path"])
    speaker_labels = job.args.get("speaker_labels")
    _step(job, cache_dir, "computing per-segment mic RMS", progress=30.0)
    t = Transcript.model_validate_json(transcript_path.read_text(encoding="utf-8"))
    group = MulticamGroup.model_validate(cache_io.read_json(group_path))
    labels = multicam.diarize_by_mic_dominance(t, group, speaker_labels)
    key = cache_io.stable_key("diarization", str(transcript_path), group.group_id)
    out = cache_io.write_json(cache_dir, "diarization", key, [l.model_dump(mode="json") for l in labels])
    counts: dict[str, int] = {}
    for l in labels:
        counts[l.speaker_label] = counts.get(l.speaker_label, 0) + 1
    summary = {"diarization_path": str(out), "segment_count": len(labels), "speaker_segment_counts": counts}
    return out, summary


def _do_pick_angle(job: jobs.Job, cache_dir: Path) -> tuple[Path, dict]:
    from roughcut_core import cache_io, multicam
    from roughcut_core.models import MulticamGroup, SpeakerLabel, Transcript
    transcript_path = Path(job.args["transcript_path"])
    diarization_path = Path(job.args["diarization_path"])
    group_path = Path(job.args["group_path"])
    reaction_interval = float(job.args.get("reaction_interval_sec", 30.0))
    reaction_hold = float(job.args.get("reaction_hold_sec", 2.0))
    _step(job, cache_dir, "picking angles", progress=50.0)
    t = Transcript.model_validate_json(transcript_path.read_text(encoding="utf-8"))
    group = MulticamGroup.model_validate(cache_io.read_json(group_path))
    diarization = [SpeakerLabel.model_validate(d) for d in cache_io.read_json(diarization_path)]
    selections = multicam.pick_angles(
        t, diarization, group,
        reaction_interval_sec=reaction_interval, reaction_hold_sec=reaction_hold,
    )
    key = cache_io.stable_key("angles", str(transcript_path), group.group_id, reaction_interval)
    out = cache_io.write_json(cache_dir, "angles", key, [a.model_dump(mode="json") for a in selections])
    summary = {
        "angles_path": str(out), "angle_count": len(selections),
        "primary_count": sum(1 for a in selections if a.reason == "primary"),
        "reaction_count": sum(1 for a in selections if a.reason == "reaction"),
        "duration_sec": round(max((a.timeline_out_sec for a in selections), default=0.0), 3),
    }
    return out, summary


def _do_test_noop(job: jobs.Job, cache_dir: Path) -> tuple[None, dict]:
    """Test-only: succeed instantly. Lets tests exercise the lifecycle
    without dragging in mlx-whisper or scipy at worker import time."""
    time.sleep(0.05)
    return None, {"echo": "ok"}


def _do_test_sleep(job: jobs.Job, cache_dir: Path) -> tuple[None, dict]:
    """Test-only: sleeps long enough to be cancelled."""
    seconds = float(job.args.get("seconds", 5))
    end = time.time() + seconds
    while time.time() < end:
        _step(job, cache_dir, f"sleeping (until {end:.1f})", progress=10.0)
        time.sleep(0.1)
    return None, {"slept": seconds}


def _do_test_boom(job: jobs.Job, cache_dir: Path) -> tuple[None, dict]:
    """Test-only: raises so we can assert traceback capture."""
    raise RuntimeError("intentional test failure")


def _do_render_cut(job: jobs.Job, cache_dir: Path) -> tuple[Path, dict]:
    from roughcut_core import render
    from roughcut_core.models import SequenceSpec
    spec = SequenceSpec.model_validate(job.args["sequence_spec"])
    output_path = Path(job.args["output_path"])
    preset = job.args.get("preset", render.DEFAULT_PRESET)
    _step(job, cache_dir, f"rendering {preset} via ffmpeg", progress=10.0)
    result = render.render_cut(spec, output_path, preset=preset)
    _step(job, cache_dir, "encode complete", progress=100.0)
    return Path(result["output_path"]), result


_HANDLERS = {
    "transcribe_video": _do_transcribe_video,
    "cluster_takes_by_silence": _do_cluster,
    "align_takes_to_script": _do_align,
    "detect_multicam_groups": _do_detect_multicam,
    "diarize_speakers": _do_diarize,
    "pick_angle_per_segment": _do_pick_angle,
    "prewarm_model": _do_prewarm_model,
    "render_cut": _do_render_cut,
    "_test_noop": _do_test_noop,
    "_test_sleep": _do_test_sleep,
    "_test_boom": _do_test_boom,
}


def _step(job: jobs.Job, cache_dir: Path, step: str, progress: float | None = None) -> None:
    job.current_step = step
    if progress is not None:
        job.progress_pct = progress
    jobs.write(cache_dir, job)


def _hint_for(exc: Exception) -> str | None:
    """Map common errors to a short actionable hint the agent can show."""
    msg = str(exc).lower()
    if "libmlx" in msg or "@rpath" in msg:
        return ("The bundled libmlx.dylib didn't load. Rebuild the .dxt — "
                "make sure mlx-metal is in scripts/build-dxt.sh's pip-download list.")
    if "ffmpeg" in msg or "ffprobe" in msg:
        return ("ffmpeg/ffprobe not found or failed. Check server/bin/ "
                "in the unpacked extension.")
    if isinstance(exc, FileNotFoundError):
        return "An input path doesn't exist. Drag the file/folder into chat again to get its absolute path."
    if isinstance(exc, NotADirectoryError):
        return "Expected a folder, got a file (or vice versa)."
    return None


if __name__ == "__main__":
    sys.exit(main())
