"""Tests for `transcribe_video` MCP tool wrapper."""

from __future__ import annotations

from pathlib import Path

from roughcut_core import transcribe
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


def test_transcribe_video_rejects_relative_path() -> None:
    res = tools._transcribe_video("relative.wav", "auto", "model", None)
    assert res.ok is False
    assert res.error == "relative_path"


def test_transcribe_video_rejects_missing_file(tmp_path: Path) -> None:
    res = tools._transcribe_video(str(tmp_path / "ghost.wav"), "auto", "model", None)
    assert res.ok is False
    assert res.error == "not_a_file"


@requires_ffmpeg
def test_transcribe_video_returns_transcript(tmp_path: Path, tiny_wav: Path, monkeypatch) -> None:
    monkeypatch.setattr(transcribe, "_run_whisper",
                        lambda p, model="", language=None: FAKE_OUTPUT)
    cache = tmp_path / "cache"
    res = tools._transcribe_video(str(tiny_wav), "auto", transcribe.DEFAULT_WHISPER_MODEL, str(cache))
    assert res.ok is True and res.error is None
    assert res.transcript is not None
    assert res.transcript.language == "en"
    assert res.transcript.segments[0].text == "Hello world."


@requires_ffmpeg
def test_transcribe_video_language_forwarded(tmp_path: Path, tiny_wav: Path, monkeypatch) -> None:
    received: dict = {}

    def fake(p, model="", language=None):
        received["language"] = language
        return FAKE_OUTPUT

    monkeypatch.setattr(transcribe, "_run_whisper", fake)
    tools._transcribe_video(str(tiny_wav), "auto", "m", str(tmp_path / "c"))
    assert received["language"] is None

    received.clear()
    tools._transcribe_video(str(tiny_wav), "es", "m", str(tmp_path / "c2"))
    assert received["language"] == "es"
