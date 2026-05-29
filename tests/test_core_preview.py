"""Tests for render_preview — low-res MP4 of a SequenceSpec for QC.

Requires ffmpeg (preview rendering shells out). Skips gracefully when
ffmpeg isn't on PATH (e.g. ubuntu-latest CI runner).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from roughcut_core import preview
from roughcut_core.models import ARollSegment, BRollInsert, SequenceSpec
from tests.conftest import requires_ffmpeg


def _testsrc(tmp_path: Path, name: str, duration: int = 3) -> Path:
    p = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x90:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + str(duration),
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)],
        check=True,
    )
    return p


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


@requires_ffmpeg
def test_render_preview_concatenates_two_segments(tmp_path: Path) -> None:
    """Two 1s segments from one 3s clip → preview duration ≈ 2.0s."""
    src = _testsrc(tmp_path, "src.mp4", duration=3)
    spec = SequenceSpec(
        aroll=[
            ARollSegment(source_path=src, source_in_sec=0.0, source_out_sec=1.0),
            ARollSegment(source_path=src, source_in_sec=1.5, source_out_sec=2.5),
        ],
    )
    out = tmp_path / "preview.mp4"
    result = preview.render_preview(spec, out)

    assert out.is_file()
    assert result["aroll_count"] == 2
    assert result["broll_count_skipped"] == 0
    assert result["size_bytes"] > 0
    # Duration declared in the response is the cut math (1.0 + 1.0 = 2.0).
    assert result["duration_sec"] == 2.0
    # Actual ffprobe duration should be within 100ms of declared.
    actual = _ffprobe_duration(out)
    assert abs(actual - 2.0) < 0.2, f"actual {actual}s vs expected 2.0s"


@requires_ffmpeg
def test_render_preview_notes_skipped_broll(tmp_path: Path) -> None:
    """B-roll inserts aren't rendered in v0.8 but the response must call it out."""
    src_a = _testsrc(tmp_path, "a.mp4", duration=2)
    src_b = _testsrc(tmp_path, "b.mp4", duration=2)
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=src_a, source_in_sec=0.0, source_out_sec=1.5)],
        broll=[BRollInsert(
            source_path=src_b, source_in_sec=0.0, source_out_sec=1.0,
            timeline_offset_sec=0.5,
        )],
    )
    out = tmp_path / "preview.mp4"
    result = preview.render_preview(spec, out)
    assert result["broll_count_skipped"] == 1
    assert result["skipped_broll_note"] is not None
    assert "B-roll" in result["skipped_broll_note"]


@requires_ffmpeg
def test_render_preview_handles_spaces_in_path(tmp_path: Path) -> None:
    """Source paths with spaces / parens must round-trip through the concat list."""
    folder = tmp_path / "Shoot Day (Tuesday)"
    folder.mkdir()
    src = _testsrc(folder, "clip with spaces.mp4", duration=2)
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=src, source_in_sec=0.0, source_out_sec=1.0)],
    )
    out = tmp_path / "preview.mp4"
    result = preview.render_preview(spec, out)
    assert out.is_file()
    assert result["size_bytes"] > 0


def test_render_preview_rejects_empty_aroll(tmp_path: Path) -> None:
    src = tmp_path / "x.mp4"; src.touch()
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=src, source_in_sec=0.0, source_out_sec=1.0)],
    )
    spec.aroll = []
    with pytest.raises(ValueError, match="aroll is empty"):
        preview.render_preview(spec, tmp_path / "out.mp4")


def test_render_preview_rejects_missing_source(tmp_path: Path) -> None:
    """If a source file doesn't exist, raise FileNotFoundError BEFORE
    we even try to shell out to ffmpeg (cryptic ffmpeg errors are useless)."""
    missing = tmp_path / "does_not_exist.mp4"
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=missing, source_in_sec=0.0, source_out_sec=1.0)],
    )
    with pytest.raises(FileNotFoundError):
        preview.render_preview(spec, tmp_path / "out.mp4")


def test_preview_cache_key_stable_across_runs(tmp_path: Path) -> None:
    """Identical spec → identical cache key. Different spec → different key."""
    src = tmp_path / "x.mp4"; src.touch()
    spec1 = SequenceSpec(
        aroll=[ARollSegment(source_path=src, source_in_sec=0.0, source_out_sec=2.0)],
    )
    spec2 = SequenceSpec(
        aroll=[ARollSegment(source_path=src, source_in_sec=0.0, source_out_sec=2.0)],
    )
    spec3 = SequenceSpec(
        aroll=[ARollSegment(source_path=src, source_in_sec=0.0, source_out_sec=3.0)],
    )
    k1 = preview.preview_cache_key(spec1, 270, 28)
    k2 = preview.preview_cache_key(spec2, 270, 28)
    k3 = preview.preview_cache_key(spec3, 270, 28)
    assert k1 == k2
    assert k1 != k3


def test_preview_cache_key_changes_with_settings(tmp_path: Path) -> None:
    src = tmp_path / "x.mp4"; src.touch()
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=src, source_in_sec=0.0, source_out_sec=2.0)],
    )
    assert preview.preview_cache_key(spec, 270, 28) != preview.preview_cache_key(
        spec, 540, 28
    )
    assert preview.preview_cache_key(spec, 270, 28) != preview.preview_cache_key(
        spec, 270, 23
    )
