"""Tests for waveform-based silence + breath detection.

Synthesizes test audio via ffmpeg lavfi (sine + anullsrc + concat) so
we know exactly where silences land. All tests gated on ffmpeg.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from roughcut_core import audio
from roughcut_core.models import Segment, Transcript, Word
from tests.conftest import requires_ffmpeg


def _make_audio(tmp_path: Path, name: str, ffmpeg_input: str, duration: float) -> Path:
    """Generate `duration` seconds of audio matching the lavfi input spec.

    `ffmpeg_input` is a string like "sine=frequency=440" or "anullsrc".
    """
    out = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"{ffmpeg_input}",
         "-t", str(duration),
         "-ac", "1", "-ar", "16000",
         "-c:a", "aac",
         str(out)],
        check=True,
    )
    return out


def _concat_audio(tmp_path: Path, name: str, parts: list[Path]) -> Path:
    """Concatenate several audio files into one. Each part is treated as-is."""
    list_file = tmp_path / f"{name}.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in parts))
    out = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c", "copy", str(out)],
        check=True,
    )
    return out


@requires_ffmpeg
def test_decode_envelope_returns_expected_length(tmp_path: Path) -> None:
    """A 2s clip → envelope length ≈ 40 frames (2.0 / 0.05)."""
    clip = _make_audio(tmp_path, "tone.m4a", "sine=frequency=440", 2.0)
    env = audio.decode_audio_envelope(clip)
    # 2.0s / 50ms = 40 frames, with some tolerance for codec padding.
    assert 38 <= len(env) <= 45, f"expected ~40 frames, got {len(env)}"


@requires_ffmpeg
def test_find_audio_silences_in_pure_silence(tmp_path: Path) -> None:
    """A 2s anullsrc clip is silent throughout."""
    clip = _make_audio(tmp_path, "silent.m4a", "anullsrc=r=16000:cl=mono", 2.0)
    result = audio.find_audio_silences(clip)
    assert len(result["silences"]) == 1
    s = result["silences"][0]
    # The whole clip should be flagged as silence (within rounding).
    assert s["start_sec"] <= 0.1
    assert s["end_sec"] >= 1.9
    assert result["silence_pct"] > 95


@requires_ffmpeg
def test_find_audio_silences_skips_audible_sections(tmp_path: Path) -> None:
    """tone-silence-tone → finds exactly one silence in the middle."""
    a = _make_audio(tmp_path, "a.m4a", "sine=frequency=440", 1.0)
    s = _make_audio(tmp_path, "s.m4a", "anullsrc=r=16000:cl=mono", 1.0)
    b = _make_audio(tmp_path, "b.m4a", "sine=frequency=880", 1.0)
    joined = _concat_audio(tmp_path, "tsb.m4a", [a, s, b])

    result = audio.find_audio_silences(joined, min_silence_sec=0.3)
    assert len(result["silences"]) == 1
    silence = result["silences"][0]
    # Silence spans roughly [1.0, 2.0] with some tolerance.
    assert 0.8 <= silence["start_sec"] <= 1.2
    assert 1.8 <= silence["end_sec"] <= 2.2
    assert silence["duration_sec"] >= 0.7


@requires_ffmpeg
def test_find_audio_silences_respects_range_filter(tmp_path: Path) -> None:
    """tone-silence-tone with range filter clips silences outside [start, end]."""
    a = _make_audio(tmp_path, "a.m4a", "sine=frequency=440", 1.0)
    s = _make_audio(tmp_path, "s.m4a", "anullsrc=r=16000:cl=mono", 1.0)
    b = _make_audio(tmp_path, "b.m4a", "sine=frequency=880", 1.0)
    joined = _concat_audio(tmp_path, "tsb.m4a", [a, s, b])

    # Only scan the first second (the tone) — should find no silence.
    result = audio.find_audio_silences(joined, start_sec=0.0, end_sec=1.0)
    assert result["silences"] == []


@requires_ffmpeg
def test_find_audio_silences_threshold_is_tunable(tmp_path: Path) -> None:
    """Higher (less negative) threshold flags more frames as silence."""
    a = _make_audio(tmp_path, "a.m4a", "sine=frequency=440", 2.0)

    strict = audio.find_audio_silences(a, threshold_db=-60.0)
    lenient = audio.find_audio_silences(a, threshold_db=-3.0)
    # A pure tone at full scale has RMS ~-3 dBFS, so strict finds
    # nothing but lenient flags the whole clip.
    assert strict["total_silence_sec"] == 0.0
    assert lenient["total_silence_sec"] > 1.5


@requires_ffmpeg
def test_envelope_can_be_passed_in_for_reuse(tmp_path: Path) -> None:
    """When the caller supplies `envelope`, decode is skipped."""
    clip = _make_audio(tmp_path, "tone.m4a", "sine=frequency=440", 2.0)
    env = audio.decode_audio_envelope(clip)
    # Use a real path but a synthetic envelope — function should not
    # re-decode (we'd notice because the result reflects the envelope).
    fake_env = np.full_like(env, -100.0)  # everything is silence
    result = audio.find_audio_silences(clip, envelope=fake_env)
    assert result["silence_pct"] > 95


@requires_ffmpeg
def test_detect_breaths_finds_low_energy_between_words(tmp_path: Path) -> None:
    """A breath sits between two words: low energy, not full silence."""
    # 0-1s tone, 1-1.5s very-quiet tone (the "breath"), 1.5-2.5s tone.
    a = _make_audio(tmp_path, "a.m4a", "sine=frequency=440:sample_rate=16000", 1.0)
    breath = _make_audio(
        tmp_path, "br.m4a",
        "sine=frequency=200:sample_rate=16000,volume=-40dB", 0.5,
    )
    b = _make_audio(tmp_path, "b.m4a", "sine=frequency=440:sample_rate=16000", 1.0)
    joined = _concat_audio(tmp_path, "abr.m4a", [a, breath, b])

    src = tmp_path / "src.mp4"
    src.touch()
    tr = Transcript(
        source_path=src, source_hash="h", duration=2.5, language="en",
        segments=[Segment(text="hello world", start=0.0, end=2.5, words=[
            Word(text="hello", start=0.0, end=1.0),
            Word(text="world", start=1.5, end=2.5),
        ])],
    )
    tp = tmp_path / "tr.json"
    tp.write_text(tr.model_dump_json())

    result = audio.detect_breaths(joined, tp)
    assert len(result["breaths"]) == 1
    br = result["breaths"][0]
    assert 0.9 <= br["start_sec"] <= 1.1
    assert 1.4 <= br["end_sec"] <= 1.6


def test_detect_breaths_rejects_missing_file(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"; src.touch()
    tr = Transcript(source_path=src, source_hash="h", duration=2.0, segments=[])
    tp = tmp_path / "tr.json"
    tp.write_text(tr.model_dump_json())
    with pytest.raises(FileNotFoundError):
        audio.detect_breaths(tmp_path / "missing.mp4", tp)


def test_decode_envelope_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        audio.decode_audio_envelope(tmp_path / "missing.mp4")
