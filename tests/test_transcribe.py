"""End-to-end test of `transcribe.transcribe` with a tiny fixture.

The real mlx-whisper call is monkeypatched out — we are testing the audio
extraction, cache key, parsing, and atomic cache write paths.
"""

from __future__ import annotations

from pathlib import Path

from roughcut import transcribe
from roughcut.models import Transcript
from tests.conftest import requires_ffmpeg


FAKE_WHISPER_OUTPUT = {
    "language": "en",
    "segments": [
        {
            "text": "Hello world.",
            "start": 0.0,
            "end": 1.0,
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.5},
                {"word": "world.", "start": 0.5, "end": 1.0},
            ],
        }
    ],
}


def test_cache_key_stable(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    assert transcribe.cache_key(f) == transcribe.cache_key(f)


def test_cache_key_changes_with_mtime(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    first = transcribe.cache_key(f)
    import os, time
    time.sleep(0.01)
    os.utime(f, None)  # bump mtime
    assert transcribe.cache_key(f) != first


@requires_ffmpeg
def test_transcribe_runs_and_caches(tmp_path: Path, tiny_wav: Path, monkeypatch) -> None:
    calls = {"n": 0}

    def fake_whisper(wav_path: Path, model: str = "") -> dict:
        calls["n"] += 1
        assert wav_path.exists()
        return FAKE_WHISPER_OUTPUT

    monkeypatch.setattr(transcribe, "_run_whisper", fake_whisper)
    cache_dir = tmp_path / "cache"

    t = transcribe.transcribe(tiny_wav, cache_dir)

    assert isinstance(t, Transcript)
    assert t.language == "en"
    assert len(t.segments) == 1
    assert t.segments[0].text == "Hello world."
    assert [w.text for w in t.segments[0].words] == ["Hello", "world."]
    assert t.duration > 0.0
    assert calls["n"] == 1

    safe_model = transcribe.DEFAULT_WHISPER_MODEL.replace("/", "_")
    cached_file = cache_dir / "transcripts" / safe_model / f"{transcribe.cache_key(tiny_wav)}.json"
    assert cached_file.exists()

    # Second call should hit the cache: whisper must not run again.
    t2 = transcribe.transcribe(tiny_wav, cache_dir)
    assert calls["n"] == 1
    assert t2.model_dump() == t.model_dump()


@requires_ffmpeg
def test_transcribe_folder_skips_unsupported(tmp_path: Path, tiny_wav: Path, monkeypatch) -> None:
    folder = tmp_path / "interviews"
    folder.mkdir()
    (folder / "notes.txt").write_text("ignore me")
    target = folder / "clip.wav"
    target.write_bytes(tiny_wav.read_bytes())

    monkeypatch.setattr(transcribe, "_run_whisper", lambda p, model="": FAKE_WHISPER_OUTPUT)

    results = transcribe.transcribe_folder(folder, tmp_path / "cache")
    assert len(results) == 1
    assert results[0].source_path.name == "clip.wav"
