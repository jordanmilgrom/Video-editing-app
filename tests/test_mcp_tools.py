"""Tests for the MCP tool layer.

Impl functions `_*` are exercised directly so tests don't have to spin
up the stdio server. A separate test verifies `build_server()` registers
every expected tool by name. Cache-dir is isolated per test via the
`ROUGHCUT_CACHE_DIR` env var so writes don't bleed into the real cache.
"""

from __future__ import annotations

import json
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


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cdir = tmp_path / "cache"
    monkeypatch.setenv("ROUGHCUT_CACHE_DIR", str(cdir))
    return cdir


def test_list_clips_rejects_relative_path(isolated_cache: Path) -> None:
    res = tools._list_clips("./relative/path", True)
    assert res.ok is False
    assert res.error == "relative_path"
    assert (res.summary or {}).get("clip_count", 0) == 0


def test_list_clips_rejects_nonexistent_folder(tmp_path: Path, isolated_cache: Path) -> None:
    missing = tmp_path / "does_not_exist"
    res = tools._list_clips(str(missing), True)
    assert res.ok is False
    assert res.error == "not_a_directory"


@requires_ffmpeg
def test_list_clips_writes_full_payload_to_disk(tmp_path: Path, isolated_cache: Path) -> None:
    _make_clip(tmp_path / "shot.mp4", duration=2, rate=24, size="320x240")
    res = tools._list_clips(str(tmp_path), True)
    assert res.ok is True and res.error is None
    summary = res.summary or {}
    assert summary["clip_count"] == 1
    assert summary["clip_paths"][0].endswith("shot.mp4")
    clips_path = Path(summary["clips_path"])
    assert clips_path.is_file()
    payload = json.loads(clips_path.read_text())
    assert len(payload) == 1
    assert payload[0]["width"] == 320 and payload[0]["height"] == 240


@requires_ffmpeg
def test_list_clips_audio_only_returns_invalid_clip_envelope(
    tmp_path: Path, isolated_cache: Path
) -> None:
    _make_audio_only(tmp_path / "audio_only.mp4")
    res = tools._list_clips(str(tmp_path), True)
    assert res.ok is False
    assert res.error == "invalid_clip"


def test_list_clips_recursive_flag_is_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_cache: Path
) -> None:
    captured: dict = {}

    def fake_list_clips(folder, recursive=True):
        captured["folder"] = folder
        captured["recursive"] = recursive
        return []

    monkeypatch.setattr(tools.clips, "list_clips", fake_list_clips)
    tools._list_clips(str(tmp_path), recursive=False)
    assert captured == {"folder": tmp_path, "recursive": False}


def test_build_server_registers_expected_tools() -> None:
    server = build_server()
    import asyncio
    tool_objs = asyncio.run(server.list_tools())
    names = {t.name for t in tool_objs}
    # Doc-mode tools
    assert {"list_clips", "transcribe_video", "cluster_takes_by_silence",
            "align_takes_to_script", "generate_fcpxml",
            "extract_frame_grid", "get_clip_thumbnail"}.issubset(names)
    # Multicam tools
    assert {"detect_multicam_groups", "diarize_speakers",
            "pick_angle_per_segment", "generate_multicam_fcpxml"}.issubset(names)
    # Meta
    assert "get_project_paths" in names


def test_get_project_paths_includes_cache_dir(
    monkeypatch: pytest.MonkeyPatch, isolated_cache: Path
) -> None:
    monkeypatch.setenv("ROUGHCUT_INTERVIEW_DIR", "/abs/interviews")
    monkeypatch.delenv("ROUGHCUT_BROLL_DIR", raising=False)
    monkeypatch.delenv("ROUGHCUT_SCRIPT_PATH", raising=False)
    res = tools._get_project_paths()
    assert res.ok is True
    s = res.summary or {}
    assert s["interview_folder"] == "/abs/interviews"
    assert s["broll_folder"] is None
    assert s["script_path"] is None
    assert s["cache_dir"] == str(isolated_cache)
