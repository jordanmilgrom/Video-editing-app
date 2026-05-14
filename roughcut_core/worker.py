"""Job worker subprocess.

Spawned by `roughcut_core.jobs.spawn()` as:
    python -m roughcut_core.worker <job_id>

Reads the persisted Job JSON, runs the tool, updates progress and the
final result_path / error fields back to the same JSON. The MCP server
never blocks; it just reads this file when the agent calls
check_job_status.

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


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("usage: python -m roughcut_core.worker <job_id>", file=sys.stderr)
        return 2

    job_id = argv[0]
    cache_dir = Path(os.environ.get("ROUGHCUT_CACHE_DIR") or (Path.home() / ".cache" / "roughcut"))
    job = jobs.read(cache_dir, job_id)
    if job is None:
        print(f"worker: job {job_id} not found", file=sys.stderr)
        return 1

    # Make Ctrl-C / SIGTERM from cancel_job land cleanly in the JSON.
    def _on_term(_sig: int, _frame: Any) -> None:
        j = jobs.read(cache_dir, job_id)
        if j and j.status in ("started", "running"):
            j.status = "cancelled"
            j.current_step = "cancelled by signal"
            jobs.write(cache_dir, j)
        sys.exit(130)

    signal.signal(signal.SIGTERM, _on_term)

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
    finally:
        jobs.write(cache_dir, job)

    return 0 if job.status == "succeeded" else 1


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
    if lang and lang.lower() == "auto":
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
    summary = {
        "transcript_path": str(transcript_path),
        "segment_count": len(t.segments),
        "duration_sec": round(t.duration, 3),
        "language": t.language,
        "model": requested_model,
        "first_200_chars": full_text[:200],
    }
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
    longest = max((len(c.segment_indices) for c in clusters), default=0)
    summary = {
        "clusters_path": str(out),
        "cluster_count": len(clusters),
        "longest_cluster_segment_count": longest,
        "total_duration_sec": round(sum(c.out_sec - c.in_sec for c in clusters), 3),
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


_HANDLERS = {
    "transcribe_video": _do_transcribe_video,
    "cluster_takes_by_silence": _do_cluster,
    "align_takes_to_script": _do_align,
    "detect_multicam_groups": _do_detect_multicam,
    "diarize_speakers": _do_diarize,
    "pick_angle_per_segment": _do_pick_angle,
    "prewarm_model": _do_prewarm_model,
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
