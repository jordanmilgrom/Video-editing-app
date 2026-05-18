"""Motion analysis for b-roll triage.

Samples downsampled grayscale frames at `sample_hz` via ffmpeg, computes
mean-absolute-pixel-difference between consecutive frames in numpy.
Returns spans tagged static / slow_pan / fast_pan / cut so the agent can
pick b-roll source_in/out_sec ranges that don't span shot boundaries or
cameraman-searching-for-the-subject panning.

Why this exists: v0.6.x b-roll selection was a coin flip. The agent saw
a 16-tile contact sheet but had no signal about *which spans were
stable*. Real-world v3 cut included long stretches of cameraman panning
back and forth across a product looking for the right framing. This
module turns that guess into a deterministic check.

Pipeline:

  ffmpeg -i input.mp4 -vf "fps=2,scale=64:36,format=gray" -f rawvideo -
            \\--------- 2 Hz, 64x36, single-channel gray ---------/
  → 2304 bytes per frame
  → numpy reshape, np.abs(diff).mean() per consecutive pair
  → bucket motion scores into labels
  → run-length compress into spans

For a 5-minute clip at 2 Hz that's 600 frames * 2304 bytes = 1.4 MB
streamed total. ffmpeg does the decode + scale; Python only does
arithmetic on tiny arrays.

Thresholds were tuned against synthetic + real footage:
  static    : score < 2     (locked-off tripod, no movement)
  slow_pan  : score < 8     (deliberate camera move, hand-held)
  fast_pan  : score < 25    (camera searching, fast whip)
  cut       : score >= 25   (shot boundary, scene change)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

SAMPLE_WIDTH = 64
SAMPLE_HEIGHT = 36  # 16:9 mini frame; 64 * 36 = 2304 bytes / frame gray
FRAME_BYTES = SAMPLE_WIDTH * SAMPLE_HEIGHT

# Score → label thresholds (mean-absolute-pixel-diff in 0..255 grayscale).
STATIC_MAX = 2.0
SLOW_PAN_MAX = 8.0
FAST_PAN_MAX = 25.0


def analyze_motion(video_path: Path, sample_hz: float = 2.0) -> dict:
    """Sample, frame-diff, and bucket motion. Returns a JSON-ready dict.

    See module docstring for the pipeline. `sample_hz` is the temporal
    resolution: 2 Hz (one frame every 500ms) is fast and accurate enough
    for "is this span stable" decisions. Bump to 4 Hz for tighter cut
    detection on fast-paced footage.
    """
    # v0.7.1: check file existence BEFORE ffmpeg presence. The file
    # check is deterministic and cheap; raising RuntimeError("ffmpeg
    # not found") for a missing file is the wrong error and broke the
    # v0.7.0 CI test (which asserts FileNotFoundError on a path that
    # doesn't exist, but the ubuntu runner has no ffmpeg so the wrong
    # check fired first).
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    raw = _extract_gray_stream(video_path, sample_hz)
    n_frames = len(raw) // FRAME_BYTES
    if n_frames < 2:
        return {
            "video_path": str(video_path),
            "sample_hz": sample_hz,
            "frame_count": n_frames,
            "spans": [],
            "stable_spans": [],
            "hint": "Clip too short to analyze (need >=2 sampled frames).",
        }

    arr = np.frombuffer(raw[: n_frames * FRAME_BYTES], dtype=np.uint8).reshape(
        n_frames, SAMPLE_HEIGHT, SAMPLE_WIDTH
    )
    # mean-abs-diff per consecutive pair → motion score for the interval
    # between frame i and i+1 (length 1/sample_hz seconds).
    diffs = np.abs(arr[1:].astype(np.int16) - arr[:-1].astype(np.int16))
    scores = diffs.mean(axis=(1, 2))

    interval = 1.0 / sample_hz
    labels = [_classify(s) for s in scores]
    spans = _runs(labels, scores, interval)

    # "Stable" spans are static or slow_pan and at least 2 seconds long.
    # The agent should pick b-roll source_in/out_sec inside these only.
    stable_spans = [
        s for s in spans
        if s["label"] in ("static", "slow_pan") and s["duration_sec"] >= 2.0
    ]

    return {
        "video_path": str(video_path),
        "sample_hz": sample_hz,
        "frame_count": n_frames,
        "duration_sampled_sec": round(n_frames * interval, 3),
        "spans": spans,
        "stable_spans": stable_spans,
        "total_static_sec": round(
            sum(s["duration_sec"] for s in spans if s["label"] == "static"), 3
        ),
        "total_slow_pan_sec": round(
            sum(s["duration_sec"] for s in spans if s["label"] == "slow_pan"), 3
        ),
        "total_fast_pan_sec": round(
            sum(s["duration_sec"] for s in spans if s["label"] == "fast_pan"), 3
        ),
        "cut_count": sum(1 for s in spans if s["label"] == "cut"),
    }


def _extract_gray_stream(video_path: Path, sample_hz: float) -> bytes:
    """Decode video into a raw-grayscale byte stream via ffmpeg."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-an",  # no audio
        "-vf", (
            f"fps={sample_hz},"
            f"scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT}:flags=fast_bilinear,"
            f"format=gray"
        ),
        "-f", "rawvideo", "-",
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True)
    return proc.stdout


def _classify(score: float) -> str:
    if score < STATIC_MAX:
        return "static"
    if score < SLOW_PAN_MAX:
        return "slow_pan"
    if score < FAST_PAN_MAX:
        return "fast_pan"
    return "cut"


def _runs(labels: list[str], scores, interval: float) -> list[dict]:
    """Run-length-compress consecutive same-label intervals into spans."""
    spans: list[dict] = []
    i = 0
    while i < len(labels):
        j = i
        # Cuts are inherently 1-frame events; don't merge consecutive cuts
        # into a long "cut" run (it'd hide N separate cuts behind one span).
        if labels[i] == "cut":
            j = i + 1
        else:
            while j < len(labels) and labels[j] == labels[i]:
                j += 1
        seg_scores = scores[i:j]
        spans.append({
            "start_sec": round(i * interval, 3),
            "end_sec": round(j * interval, 3),
            "duration_sec": round((j - i) * interval, 3),
            "label": labels[i],
            "mean_motion_score": round(float(seg_scores.mean()), 3),
        })
        i = j
    return spans
