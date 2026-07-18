"""Shot / scene boundary detection with a representative frame per shot.

Composes two existing capabilities: `motion.analyze_motion` (motion
spans tagged static / slow_pan / fast_pan / cut) and `broll.extract_frame`
(one frame from a specific timecode).

A "shot" here is the interval between consecutive `cut` events (or
between clip start / end and the nearest cut). For each shot we extract
one frame from the middle of the shot as a visual thumbnail, so the
agent can vision-read the returned list to identify each shot's content
without opening the clip in a viewer.

Deterministic. No LLM imports. Rep frames are cached under
`cache/scene-frames/<key>.jpg` so re-running `detect_scenes` on the
same clip is a no-op.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roughcut_core import broll, cache_io, motion, transcribe


def detect_scenes(
    video_path: Path,
    cache_dir: Path,
    *,
    sample_hz: float = 2.0,
    min_shot_sec: float = 1.0,
) -> dict[str, Any]:
    """Return a list of shots + one representative frame per shot.

    Uses `motion.analyze_motion`'s `cut`-labeled spans as boundaries.
    Shots shorter than `min_shot_sec` are dropped (usually noise around
    a real cut, not a real shot).
    """
    video_path = Path(video_path)
    result = motion.analyze_motion(video_path, sample_hz=sample_hz)
    spans = result.get("spans", [])
    duration = result.get("duration_sampled_sec", 0.0)

    if not spans:
        return {
            "video_path": str(video_path),
            "sample_hz": sample_hz,
            "duration_sec": duration,
            "shot_count": 0,
            "shots": [],
            "hint": result.get(
                "hint",
                "No motion spans found — clip may be too short.",
            ),
        }

    # Cut boundaries: the START of each `cut` span is where the previous
    # shot ended AND the next shot began (a hard cut is instantaneous but
    # the frame-diff labels one interval as `cut`).
    boundaries: list[float] = [0.0]
    for s in spans:
        if s["label"] == "cut":
            boundaries.append(s["start_sec"])
    boundaries.append(duration)
    # Dedup + sort (small numeric floats — dedupe by rounding).
    seen: set[float] = set()
    uniq: list[float] = []
    for b in sorted(boundaries):
        key = round(b, 3)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(key)

    sheet_dir = Path(cache_dir) / "scene-frames"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    file_key = transcribe.cache_key(video_path)

    shots: list[dict[str, Any]] = []
    for i in range(len(uniq) - 1):
        shot_start = uniq[i]
        shot_end = uniq[i + 1]
        shot_duration = shot_end - shot_start
        if shot_duration < min_shot_sec:
            continue
        # Rep frame at the middle of the shot (dodges cut-transition frames
        # at the boundaries).
        rep_at = round(shot_start + shot_duration / 2.0, 3)
        rep_path = sheet_dir / f"{file_key}_shot{i:03d}_{int(rep_at * 1000)}.jpg"
        if not rep_path.exists():
            try:
                broll.extract_frame(video_path, rep_at, rep_path)
            except Exception:  # noqa: BLE001
                # Frame extraction failure isn't fatal — still return the shot
                # with rep_frame_path=None so the agent knows the boundary
                # info is trustworthy even if the visual isn't.
                shots.append({
                    "shot_index": len(shots),
                    "start_sec": shot_start,
                    "end_sec": shot_end,
                    "duration_sec": round(shot_duration, 3),
                    "rep_at_sec": rep_at,
                    "rep_frame_path": None,
                })
                continue
        shots.append({
            "shot_index": len(shots),
            "start_sec": shot_start,
            "end_sec": shot_end,
            "duration_sec": round(shot_duration, 3),
            "rep_at_sec": rep_at,
            "rep_frame_path": str(rep_path),
        })

    return {
        "video_path": str(video_path),
        "sample_hz": sample_hz,
        "duration_sec": duration,
        "min_shot_sec": min_shot_sec,
        "shot_count": len(shots),
        "shots": shots,
        "cut_count": result.get("cut_count", 0),
    }
