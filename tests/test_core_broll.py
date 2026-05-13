"""Tests for b-roll frame extraction + contact-sheet tiling.

Deterministic. No LLM. The MCP layer is what exposes these to an agent;
that wiring is tested separately.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from roughcut_core import broll
from tests.conftest import requires_ffmpeg


@pytest.fixture
def tiny_video(tmp_path: Path) -> Path:
    """A 2-second synthetic 320x240 test video — small but real."""
    out = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=12",
         "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )
    return out


def test_frame_timestamps_inset_and_evenly_spaced() -> None:
    # 16s clip, 4 frames: edge=0.5, span 0.5..15.5, step=5.0
    ts = broll._frame_timestamps(16.0, n=4)
    assert ts == [0.5, 5.5, 10.5, 15.5]


def test_frame_timestamps_short_clip_stays_in_bounds() -> None:
    # 2s clip: edge=0.1, every frame should be inside (0.1, 1.9].
    ts = broll._frame_timestamps(2.0, n=16)
    assert all(0.1 <= t <= 1.9 for t in ts)


@requires_ffmpeg
def test_build_contact_sheet_dimensions(tmp_path: Path, tiny_video: Path) -> None:
    out = tmp_path / "sheet.png"
    timestamps = broll.build_contact_sheet(tiny_video, out, duration=2.0)
    assert out.exists()
    assert len(timestamps) == broll.NUM_FRAMES
    with Image.open(out) as img:
        assert img.size == (broll.CELL_W * broll.GRID_COLS, broll.CELL_H * broll.GRID_ROWS)


@requires_ffmpeg
def test_build_contact_sheet_without_overlay(tmp_path: Path, tiny_video: Path) -> None:
    out = tmp_path / "sheet_clean.png"
    timestamps = broll.build_contact_sheet(
        tiny_video, out, duration=2.0, overlay_timecodes=False
    )
    assert out.exists() and len(timestamps) == broll.NUM_FRAMES
