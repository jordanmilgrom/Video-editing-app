"""Tests for the extract_frame_grid and get_clip_thumbnail vision tools."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from mcp.server.fastmcp.utilities.types import Image
from mcp.types import TextContent

from roughcut_mcp import tools
from roughcut_mcp.responses import ToolResponse
from tests.conftest import requires_ffmpeg


@pytest.fixture
def tiny_video(tmp_path: Path) -> Path:
    out = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=12",
         "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )
    return out


def test_extract_frame_grid_rejects_relative_path() -> None:
    res = tools._extract_frame_grid("relative.mp4", 16, True)
    assert isinstance(res, ToolResponse)
    assert res.ok is False
    assert res.error == "relative_path"


@requires_ffmpeg
def test_extract_frame_grid_returns_image_and_sidecar(
    tmp_path: Path, tiny_video: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ROUGHCUT_CACHE_DIR", str(tmp_path / "cache"))
    result = tools._extract_frame_grid(str(tiny_video), 16, True)
    assert isinstance(result, list) and len(result) == 2
    image, sidecar = result
    assert isinstance(image, Image)
    assert isinstance(sidecar, TextContent)
    payload = json.loads(sidecar.text)
    assert "image_path" in payload and "duration_sec" in payload
    assert payload["num_frames"] == 16
    assert Path(payload["image_path"]).exists()


@requires_ffmpeg
def test_extract_frame_grid_caches(tmp_path: Path, tiny_video: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROUGHCUT_CACHE_DIR", str(tmp_path / "cache"))
    calls = {"n": 0}
    real_build = tools.broll.build_contact_sheet

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(tools.broll, "build_contact_sheet", counted)
    tools._extract_frame_grid(str(tiny_video), 16, True)
    tools._extract_frame_grid(str(tiny_video), 16, True)  # second call should hit cache
    assert calls["n"] == 1


def test_get_clip_thumbnail_rejects_relative_path() -> None:
    res = tools._get_clip_thumbnail("relative.mp4", 0.5)
    assert isinstance(res, ToolResponse) and res.error == "relative_path"


def test_get_clip_thumbnail_rejects_negative_timecode(tmp_path: Path, monkeypatch) -> None:
    # Build any abs file so abs_file passes; timecode validation runs after.
    f = tmp_path / "x.mp4"
    f.write_bytes(b"x")
    res = tools._get_clip_thumbnail(str(f), -1.0)
    assert isinstance(res, ToolResponse) and res.error == "invalid_spec"


@requires_ffmpeg
def test_get_clip_thumbnail_returns_image(tmp_path: Path, tiny_video: Path, monkeypatch) -> None:
    # Force cache_dir into tmp_path so we don't write to ~/.cache.
    monkeypatch.setenv("ROUGHCUT_CACHE_DIR", str(tmp_path / "cache"))
    result = tools._get_clip_thumbnail(str(tiny_video), 1.0)
    assert isinstance(result, list) and len(result) == 2
    image, sidecar = result
    assert isinstance(image, Image) and isinstance(sidecar, TextContent)
    payload = json.loads(sidecar.text)
    assert Path(payload["image_path"]).exists()
    assert payload["timecode_sec"] == 1.0
