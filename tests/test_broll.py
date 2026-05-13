"""Tests for b-roll analysis. Real ffmpeg + PIL run; Claude is monkeypatched."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from roughcut import broll, claude
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


FAKE_DESCRIBE = {
    "subject": "color bars test pattern",
    "motion": "static",
    "mood": "neutral",
    "tags": ["test", "synthetic", "pattern"],
    "suggested_in_sec": 0.5,
    "suggested_out_sec": 1.5,
    "description": "Synthetic SMPTE-style test pattern.",
}


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
def test_analyze_clip_caches(tmp_path: Path, tiny_video: Path, monkeypatch) -> None:
    cache_dir = tmp_path / "cache"
    calls = {"n": 0}

    def fake_vision(prompt_name, image_path, schema, *, tool_name=claude.DEFAULT_TOOL_NAME, **vars):
        calls["n"] += 1
        assert image_path.exists()
        return FAKE_DESCRIBE

    monkeypatch.setattr(claude, "call_vision", fake_vision)

    clip = broll.analyze_clip(tiny_video, cache_dir)
    assert clip.subject == "color bars test pattern"
    assert clip.tags == ["test", "synthetic", "pattern"]
    assert clip.suggested_out_sec <= clip.duration
    assert calls["n"] == 1

    cached = cache_dir / "broll" / f"{clip.source_hash}.json"
    assert cached.exists()

    # Rerun: cache hit, no second vision call.
    clip2 = broll.analyze_clip(tiny_video, cache_dir)
    assert clip2.model_dump() == clip.model_dump()
    assert calls["n"] == 1


@requires_ffmpeg
def test_analyze_folder_filters_extensions(tmp_path: Path, tiny_video: Path, monkeypatch) -> None:
    folder = tmp_path / "broll"
    folder.mkdir()
    target = folder / "shot1.mp4"
    target.write_bytes(tiny_video.read_bytes())
    (folder / "notes.txt").write_text("ignore")

    monkeypatch.setattr(
        claude, "call_vision",
        lambda *a, **k: FAKE_DESCRIBE,
    )
    out = broll.analyze_folder(folder, tmp_path / "cache")
    assert len(out) == 1
    only = next(iter(out.values()))
    assert only.source_path.name == "shot1.mp4"
