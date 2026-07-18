"""Tests for `roughcut_core.scenes.detect_scenes`."""

from __future__ import annotations

import subprocess
from pathlib import Path

from roughcut_core import scenes
from tests.conftest import requires_ffmpeg


def _make_two_shot_clip(path: Path, *, cache_dir: Path) -> Path:
    """testsrc for 3s + smptebars for 3s → concat → one obvious cut halfway."""
    p1 = cache_dir / "a.mp4"
    p2 = cache_dir / "b.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=24",
         "-pix_fmt", "yuv420p", str(p1)], check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "smptebars=duration=3:size=320x240:rate=24",
         "-pix_fmt", "yuv420p", str(p2)], check=True,
    )
    lst = cache_dir / "list.txt"
    lst.write_text(f"file '{p1}'\nfile '{p2}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(path)], check=True,
    )
    return path


@requires_ffmpeg
def test_detect_scenes_finds_boundary_and_rep_frames(tmp_path: Path) -> None:
    clip = _make_two_shot_clip(tmp_path / "combined.mp4", cache_dir=tmp_path)
    result = scenes.detect_scenes(
        clip, tmp_path / "cache", sample_hz=4.0, min_shot_sec=0.5,
    )
    assert result["shot_count"] >= 2
    for s in result["shots"]:
        assert s["start_sec"] < s["end_sec"]
        assert s["duration_sec"] >= 0.5
        assert s["rep_frame_path"] is not None
        assert Path(s["rep_frame_path"]).is_file()


@requires_ffmpeg
def test_detect_scenes_min_shot_sec_filters(tmp_path: Path) -> None:
    clip = _make_two_shot_clip(tmp_path / "combined.mp4", cache_dir=tmp_path)
    strict = scenes.detect_scenes(
        clip, tmp_path / "cache", sample_hz=4.0, min_shot_sec=10.0,
    )
    # Nothing survives a min_shot_sec of 10 when each shot is ~3s.
    assert strict["shot_count"] == 0


@requires_ffmpeg
def test_detect_scenes_caches_rep_frames(tmp_path: Path) -> None:
    clip = _make_two_shot_clip(tmp_path / "combined.mp4", cache_dir=tmp_path)
    r1 = scenes.detect_scenes(
        clip, tmp_path / "cache", sample_hz=4.0, min_shot_sec=0.5,
    )
    first_mtime = Path(r1["shots"][0]["rep_frame_path"]).stat().st_mtime
    r2 = scenes.detect_scenes(
        clip, tmp_path / "cache", sample_hz=4.0, min_shot_sec=0.5,
    )
    assert Path(r2["shots"][0]["rep_frame_path"]).stat().st_mtime == first_mtime
