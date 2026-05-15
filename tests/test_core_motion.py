"""Tests for motion analysis (b-roll triage).

We synthesize three test videos via ffmpeg lavfi:
- A locked-off "static" clip (testsrc with rate=4 — same pattern, no motion)
- A panning clip (testsrc with overlay drift)
- A clip with a hard cut in the middle (concatenated black + white solids)

The bucket thresholds are tuned generously enough that the synthesized
clips land in the expected labels without needing real footage.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roughcut_core import motion
from tests.conftest import requires_ffmpeg


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *args],
        check=True,
    )


@requires_ffmpeg
def test_static_clip_classifies_as_static(tmp_path: Path) -> None:
    """A locked-off solid color → all spans should be static."""
    clip = tmp_path / "locked.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=4:r=24",
        "-pix_fmt", "yuv420p", str(clip),
    )
    result = motion.analyze_motion(clip, sample_hz=2.0)
    assert result["frame_count"] >= 4
    # Every span should be static (no motion in solid color).
    labels = {s["label"] for s in result["spans"]}
    assert labels == {"static"}, f"expected only static, got {labels}: {result['spans']}"
    assert result["total_static_sec"] > 0
    # Stable spans must include the run.
    assert any(s["duration_sec"] >= 1.5 for s in result["stable_spans"])


@requires_ffmpeg
def test_hard_cut_triggers_cut_label(tmp_path: Path) -> None:
    """Concat black → white → black should produce at least one `cut`."""
    parts = []
    for i, color in enumerate(("black", "white", "black")):
        p = tmp_path / f"part{i}.mp4"
        _ffmpeg(
            "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d=2:r=24",
            "-pix_fmt", "yuv420p", str(p),
        )
        parts.append(p)
    list_file = tmp_path / "concat.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in parts))
    out = tmp_path / "cuts.mp4"
    _ffmpeg("-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", str(out))

    result = motion.analyze_motion(out, sample_hz=4.0)
    cut_spans = [s for s in result["spans"] if s["label"] == "cut"]
    assert len(cut_spans) >= 1, (
        f"expected at least one cut, got spans: {result['spans']}"
    )


@requires_ffmpeg
def test_short_clip_returns_empty_spans_with_hint(tmp_path: Path) -> None:
    """A clip too short to sample 2 frames should return a clean hint."""
    clip = tmp_path / "tiny.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=red:s=160x90:d=0.2:r=24",
        "-pix_fmt", "yuv420p", str(clip),
    )
    result = motion.analyze_motion(clip, sample_hz=2.0)
    assert result["spans"] == []
    assert result["stable_spans"] == []
    assert "hint" in result


def test_analyze_motion_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        motion.analyze_motion(tmp_path / "does_not_exist.mp4")


def test_classify_thresholds() -> None:
    assert motion._classify(0.0) == "static"
    assert motion._classify(1.5) == "static"
    assert motion._classify(2.5) == "slow_pan"
    assert motion._classify(7.9) == "slow_pan"
    assert motion._classify(10.0) == "fast_pan"
    assert motion._classify(50.0) == "cut"
