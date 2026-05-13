"""Tests for the ffprobe-based clip inventory."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roughcut_core import clips
from tests.conftest import requires_ffmpeg


def _make_clip(path: Path, *, duration: int = 2, rate: int = 24, size: str = "320x240") -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate={rate}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def test_parse_rational() -> None:
    assert clips._parse_rational("24000/1001") == pytest.approx(23.976, rel=1e-3)
    assert clips._parse_rational("24/1") == 24.0
    assert clips._parse_rational("0/1") == 0.0
    assert clips._parse_rational("nonsense") == 0.0


@requires_ffmpeg
def test_probe_clip_returns_metadata(tmp_path: Path) -> None:
    clip_path = tmp_path / "shot.mp4"
    _make_clip(clip_path, duration=2, rate=24, size="320x240")

    meta = clips.probe_clip(clip_path)
    assert meta.path == clip_path.resolve()
    assert meta.duration_sec == pytest.approx(2.0, abs=0.1)
    assert meta.fps == pytest.approx(24.0, abs=0.01)
    assert (meta.width, meta.height) == (320, 240)
    assert meta.codec  # ffmpeg picks something
    assert meta.size_bytes > 0


@requires_ffmpeg
def test_list_clips_filters_and_sorts(tmp_path: Path) -> None:
    folder = tmp_path / "broll"
    folder.mkdir()
    _make_clip(folder / "b.mp4")
    _make_clip(folder / "a.mp4")
    (folder / "notes.txt").write_text("ignore")

    metas = clips.list_clips(folder)
    assert [m.path.name for m in metas] == ["a.mp4", "b.mp4"]


@requires_ffmpeg
def test_list_clips_recursive_toggle(tmp_path: Path) -> None:
    root = tmp_path / "broll"
    nested = root / "day_one"
    nested.mkdir(parents=True)
    _make_clip(root / "top.mp4")
    _make_clip(nested / "inner.mp4")

    recursive = clips.list_clips(root, recursive=True)
    flat = clips.list_clips(root, recursive=False)

    assert sorted(m.path.name for m in recursive) == ["inner.mp4", "top.mp4"]
    assert [m.path.name for m in flat] == ["top.mp4"]


def test_list_clips_rejects_nonexistent_folder(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        clips.list_clips(tmp_path / "missing")
