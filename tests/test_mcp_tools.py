"""Tests for the MCP tool layer.

The implementation function `_list_clips` is exercised directly so we
don't have to spin up the stdio server. A separate test verifies that
`build_server()` registers the tool by name.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roughcut_mcp import tools
from roughcut_mcp.server import build_server
from tests.conftest import requires_ffmpeg


def _make_clip(path: Path, *, duration: int = 2, rate: int = 24, size: str = "320x240") -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate={rate}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _make_audio_only(path: Path, *, duration: int = 1) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono",
         "-t", str(duration), "-c:a", "aac", str(path)],
        check=True,
    )


def test_list_clips_rejects_relative_path() -> None:
    res = tools._list_clips("./relative/path", True)
    assert res.ok is False
    assert res.error == "relative_path"
    assert res.clips == []
    assert "relative" in (res.message or "").lower() or "./relative" in (res.message or "")


def test_list_clips_rejects_nonexistent_folder(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    res = tools._list_clips(str(missing), True)
    assert res.ok is False
    assert res.error == "not_a_directory"


@requires_ffmpeg
def test_list_clips_returns_clip_metadata(tmp_path: Path) -> None:
    _make_clip(tmp_path / "shot.mp4", duration=2, rate=24, size="320x240")
    res = tools._list_clips(str(tmp_path), True)
    assert res.ok is True and res.error is None
    assert len(res.clips) == 1
    meta = res.clips[0]
    assert meta.path.name == "shot.mp4"
    assert (meta.width, meta.height) == (320, 240)
    assert meta.fps == pytest.approx(24.0, abs=0.01)


@requires_ffmpeg
def test_list_clips_audio_only_returns_invalid_clip_envelope(tmp_path: Path) -> None:
    _make_audio_only(tmp_path / "audio_only.mp4")
    res = tools._list_clips(str(tmp_path), True)
    # Core raises ValueError("No video stream in ...") which the MCP
    # wrapper maps to the structured envelope, NOT a raised exception.
    assert res.ok is False
    assert res.error == "invalid_clip"
    assert "video stream" in (res.message or "").lower()


def test_list_clips_recursive_flag_is_forwarded(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_list_clips(folder, recursive=True):
        captured["folder"] = folder
        captured["recursive"] = recursive
        return []

    monkeypatch.setattr(tools.clips, "list_clips", fake_list_clips)
    tools._list_clips(str(tmp_path), recursive=False)
    assert captured == {"folder": tmp_path, "recursive": False}


def test_build_server_registers_list_clips() -> None:
    server = build_server()
    # FastMCP keeps tools accessible via its async list_tools() method.
    import asyncio

    tool_objs = asyncio.run(server.list_tools())
    names = {t.name for t in tool_objs}
    assert "list_clips" in names
