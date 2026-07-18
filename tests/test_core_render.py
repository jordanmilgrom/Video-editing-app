"""Tests for `roughcut_core.render.render_cut`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roughcut_core import render
from roughcut_core.models import ARollSegment, SequenceSpec
from tests.conftest import requires_ffmpeg


def _make_av_clip(path: Path, *, duration: int = 2) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=640x360:rate=24",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-shortest", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-c:a", "aac", str(path)],
        check=True,
    )


def test_render_cut_rejects_missing_source(tmp_path: Path) -> None:
    """File-existence check runs BEFORE ffmpeg-on-PATH — matches v0.7.1 pattern."""
    missing = tmp_path / "does_not_exist.mp4"
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=missing, source_in_sec=0.0, source_out_sec=1.0)],
    )
    with pytest.raises(FileNotFoundError):
        render.render_cut(spec, tmp_path / "out.mp4")


def test_render_cut_rejects_empty_spec(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="aroll is empty"):
        render.render_cut(SequenceSpec(aroll=[]), tmp_path / "out.mp4")


def test_render_cut_rejects_unknown_preset(tmp_path: Path) -> None:
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"not a real mp4")
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=fake, source_in_sec=0.0, source_out_sec=1.0)],
    )
    with pytest.raises(ValueError, match="unknown preset"):
        render.render_cut(spec, tmp_path / "out.mp4", preset="8k144")


@requires_ffmpeg
def test_render_cut_produces_playable_mp4(tmp_path: Path) -> None:
    clip = tmp_path / "src.mp4"
    _make_av_clip(clip, duration=2)
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=clip, source_in_sec=0.0, source_out_sec=1.5)],
    )
    out = tmp_path / "delivered.mp4"
    # 720p30 keeps the test fast (still H.264/AAC delivery).
    result = render.render_cut(spec, out, preset="720p30")
    assert result["preset"] == "720p30"
    assert result["width"] == 1280 and result["height"] == 720
    assert result["fps"] == 30
    assert out.is_file() and out.stat().st_size > 0
    # ffprobe the output to confirm it's a real MP4 with video + audio.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height",
         "-of", "default=nokey=1:noprint_wrappers=1", str(out)],
        capture_output=True, text=True, check=True,
    )
    lines = probe.stdout.strip().splitlines()
    assert "h264" in lines
    assert "aac" in lines
    assert "1280" in lines and "720" in lines
