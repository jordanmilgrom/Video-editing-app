"""Tests for the transcribe_video tool.

The MCP wrapper is now a thin spawner; the actual transcription logic
lives in roughcut_core.worker._do_transcribe_video. We test the worker
handler directly (mocking mlx-whisper) and the MCP wrapper's validation
behavior separately.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from roughcut_core import jobs, transcribe, worker
from roughcut_mcp import tools
from tests.conftest import requires_ffmpeg

FAKE_OUTPUT = {
    "language": "en",
    "segments": [
        {"text": "Hello world.", "start": 0.0, "end": 1.0,
         "words": [{"word": "Hello", "start": 0.0, "end": 0.5},
                   {"word": "world.", "start": 0.5, "end": 1.0}]},
    ],
}


def _make_job(tmp_path: Path, args: dict) -> jobs.Job:
    now = time.time()
    return jobs.Job(
        job_id="test", tool_name="transcribe_video", args=args,
        status="running", started_at=now, updated_at=now,
    )


def test_path_validation_rejects_relative_path() -> None:
    from roughcut_mcp.responses import abs_file
    bad = abs_file("relative.wav")
    assert hasattr(bad, "ok") and bad.ok is False
    assert bad.error == "relative_path"


def test_path_validation_rejects_missing_file(tmp_path: Path) -> None:
    from roughcut_mcp.responses import abs_file
    bad = abs_file(str(tmp_path / "ghost.wav"))
    assert hasattr(bad, "ok") and bad.ok is False
    assert bad.error == "not_a_file"


@requires_ffmpeg
def test_worker_handler_writes_transcript_and_summary(
    tmp_path: Path, tiny_wav: Path, monkeypatch
) -> None:
    monkeypatch.setattr(transcribe, "_run_whisper",
                        lambda p, model="", language=None: FAKE_OUTPUT)
    cache = tmp_path / "cache"
    cache.mkdir()
    job = _make_job(tmp_path, {
        "video_path": str(tiny_wav), "language": "auto",
        "model": transcribe.DEFAULT_WHISPER_MODEL,
    })
    result_path, summary = worker._do_transcribe_video(job, cache)
    assert summary["language"] == "en"
    assert summary["segment_count"] == 1
    assert "Hello world." in summary["first_200_chars"]
    assert Path(summary["transcript_path"]).is_file()


@requires_ffmpeg
def test_worker_handler_forwards_language(
    tmp_path: Path, tiny_wav: Path, monkeypatch
) -> None:
    received: dict = {}

    def fake(p, model="", language=None):
        received["language"] = language
        return FAKE_OUTPUT

    monkeypatch.setattr(transcribe, "_run_whisper", fake)
    cache = tmp_path / "c"
    cache.mkdir()
    worker._do_transcribe_video(_make_job(tmp_path, {
        "video_path": str(tiny_wav), "language": "auto", "model": "mlx-community/whisper-small-mlx",
    }), cache)
    assert received["language"] is None

    received.clear()
    cache2 = tmp_path / "c2"
    cache2.mkdir()
    worker._do_transcribe_video(_make_job(tmp_path, {
        "video_path": str(tiny_wav), "language": "es", "model": "mlx-community/whisper-small-mlx",
    }), cache2)
    assert received["language"] == "es"
