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
    # Doc-mode tools (sync + async)
    assert {"list_clips", "transcribe_video", "cluster_takes_by_silence",
            "align_takes_to_script", "generate_fcpxml",
            "extract_frame_grid", "get_clip_thumbnail"}.issubset(names)
    # Multicam tools
    assert {"detect_multicam_groups", "diarize_speakers",
            "pick_angle_per_segment", "generate_multicam_fcpxml"}.issubset(names)
    # Job management (new in v0.6.0)
    assert {"check_job_status", "list_jobs", "cancel_job", "resume_job"}.issubset(names)
    # Meta
    assert {"get_project_paths", "get_system_status"}.issubset(names)
    # Documentary mode (new in v0.6.3)
    assert {"read_transcript", "search_transcripts", "summarize_clip"}.issubset(names)
    # Prewarm tool (v0.6.1)
    assert "prewarm_model" in names
    # v0.6.4: self-heal
    assert "restart_workers" in names
    # v0.6.5: validation + lookup + log tail
    assert {"validate_fcpxml", "lookup_transcript_by_video_path",
            "get_server_logs"}.issubset(names)
    # v0.7.0: editorial intelligence — silence-finding + motion analysis
    assert {"find_silences", "analyze_motion"}.issubset(names)
    # v0.8.0: editorial intelligence II — fillers + captions + preview + handles
    assert {"detect_fillers", "describe_clip", "search_broll",
            "render_preview", "add_handles_to_spec"}.issubset(names)
    # v0.9.0: waveform-based tightening + fused tighten_take
    assert {"find_audio_silences", "detect_breaths",
            "detect_false_starts", "tighten_take"}.issubset(names)
    # v0.10.0: multi-modal segment watching
    assert "watch_segment" in names
    # v0.11.0: batch/project + delivery + OTIO + scenes
    assert {"transcribe_folder", "index_project",
            "detect_scenes", "render_cut"}.issubset(names)
    # v0.12.0: structured scene analysis (Level-2 video understanding)
    assert {"analyze_scene", "save_scene_analysis",
            "read_scene_analysis", "search_scenes"}.issubset(names)


def test_get_system_status_reports_ffmpeg_when_available(isolated_cache: Path) -> None:
    res = tools._get_system_status()
    assert res.summary is not None
    details = res.summary["details"]
    # ffmpeg/ffprobe live on the test host's PATH thanks to the conftest fixture.
    assert "ffmpeg" in details
    assert "python" in details
    assert details["python"]["ok"] is True


def test_check_job_status_returns_not_a_file_for_unknown_id(isolated_cache: Path) -> None:
    res = tools._check_job_status("does-not-exist")
    assert res.ok is False
    assert res.error == "not_a_file"
    assert "hint" in (res.summary or {})


def test_list_jobs_returns_empty_when_cache_is_fresh(isolated_cache: Path) -> None:
    res = tools._list_jobs(None, 100, 0)
    assert res.ok is True
    # v0.6.4: pagination — total_count + returned_count instead of job_count.
    assert (res.summary or {})["total_count"] == 0
    assert (res.summary or {})["returned_count"] == 0
    assert (res.summary or {})["next_offset"] is None


def test_resume_job_returns_succeeded_immediately_when_cached(
    isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roughcut_core import jobs as core_jobs
    import time
    now = time.time()
    j = core_jobs.Job(
        job_id="cached-ok", tool_name="_test_noop", args={},
        status="succeeded", started_at=now, updated_at=now,
        result_path="/tmp/x.json", result_summary={"echo": "ok"},
    )
    core_jobs.write(isolated_cache, j)
    res = tools._resume_job("cached-ok")
    assert res.ok is True
    assert res.summary["status"] == "succeeded"
    assert res.summary["result_path"] == "/tmp/x.json"


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
