"""Tests for add_handles_to_spec — source-handle padding for NLE editability."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roughcut_core import handles
from roughcut_core.models import ARollSegment, BRollInsert, SequenceSpec
from tests.conftest import requires_ffmpeg


def test_handles_pads_both_in_and_out(tmp_path: Path) -> None:
    """Default handle_sec=0.5 should subtract 0.5 from in and add 0.5 to out."""
    src = tmp_path / "interview.mp4"
    src.touch()
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=src, source_in_sec=1.0, source_out_sec=3.0)],
    )
    padded = handles.add_handles_to_spec(spec, handle_sec=0.5)
    seg = padded.aroll[0]
    assert seg.source_in_sec == 0.5
    assert seg.source_out_sec == 3.5
    # Original spec unchanged (no mutation).
    assert spec.aroll[0].source_in_sec == 1.0
    assert spec.aroll[0].source_out_sec == 3.0


def test_handles_clamp_at_zero(tmp_path: Path) -> None:
    """source_in_sec=0.3 with handle_sec=0.5 must clamp to 0, not go negative."""
    src = tmp_path / "interview.mp4"
    src.touch()
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=src, source_in_sec=0.3, source_out_sec=2.0)],
    )
    padded = handles.add_handles_to_spec(spec, handle_sec=0.5)
    assert padded.aroll[0].source_in_sec == 0.0
    assert padded.aroll[0].source_out_sec == 2.5


@requires_ffmpeg
def test_handles_clamp_at_file_duration(tmp_path: Path) -> None:
    """source_out_sec near the end of a real file must clamp to file duration."""
    clip = tmp_path / "real.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=3:size=160x90:rate=24",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=clip, source_in_sec=1.0, source_out_sec=2.8)],
    )
    padded = handles.add_handles_to_spec(spec, handle_sec=1.0)
    seg = padded.aroll[0]
    # source_in: 1.0 - 1.0 = 0.0 (clamps at 0)
    # source_out: 2.8 + 1.0 = 3.8, clamped to ~3.0 (file duration)
    assert seg.source_in_sec == 0.0
    assert seg.source_out_sec <= 3.01  # 50ms slack for probe rounding
    assert seg.source_out_sec >= 2.95


def test_handles_pads_broll(tmp_path: Path) -> None:
    src_a = tmp_path / "a.mp4"; src_a.touch()
    src_b = tmp_path / "b.mp4"; src_b.touch()
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=src_a, source_in_sec=1.0, source_out_sec=3.0)],
        broll=[BRollInsert(
            source_path=src_b, source_in_sec=2.0, source_out_sec=4.0,
            timeline_offset_sec=1.0,
        )],
    )
    padded = handles.add_handles_to_spec(spec, handle_sec=0.25)
    ins = padded.broll[0]
    assert ins.source_in_sec == 1.75
    assert ins.source_out_sec == 4.25
    # timeline_offset_sec must NOT change — the cut still lands at the same
    # timeline position; only the source-media window grows.
    assert ins.timeline_offset_sec == 1.0


def test_handles_zero_is_identity(tmp_path: Path) -> None:
    src = tmp_path / "interview.mp4"
    src.touch()
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=src, source_in_sec=1.0, source_out_sec=3.0)],
    )
    padded = handles.add_handles_to_spec(spec, handle_sec=0.0)
    assert padded.aroll[0].source_in_sec == 1.0
    assert padded.aroll[0].source_out_sec == 3.0


def test_handles_rejects_negative() -> None:
    src = Path("/tmp/x.mp4")
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=src, source_in_sec=1.0, source_out_sec=2.0)],
    )
    with pytest.raises(ValueError, match="handle_sec must be >= 0"):
        handles.add_handles_to_spec(spec, handle_sec=-0.1)
