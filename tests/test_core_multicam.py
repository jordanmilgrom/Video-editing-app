"""Multicam tests using numpy-synthesized WAVs of known offsets and amplitudes.

We write WAVs by hand rather than relying on ffmpeg lavfi expressions —
the resulting fixtures are deterministic, fast, and avoid ffmpeg's
positional argument quirks for `-af` between multiple inputs.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from roughcut_core import multicam
from roughcut_core.models import MulticamGroup, Segment, SpeakerLabel, Transcript
from tests.conftest import requires_ffmpeg

SR = 16_000


def _write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> None:
    """Write float32 [-1, 1] samples as 16-bit mono PCM WAV."""
    pcm = np.clip(samples, -1.0, 1.0)
    pcm16 = (pcm * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.tobytes())


def _tone(duration_sec: float, freq: float = 440.0, sr: int = SR) -> np.ndarray:
    t = np.arange(int(duration_sec * sr)) / sr
    return 0.3 * np.sin(2 * np.pi * freq * t).astype(np.float32)


def _silence(duration_sec: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(duration_sec * sr), dtype=np.float32)


@requires_ffmpeg
def test_detect_groups_finds_two_synced_files(tmp_path: Path) -> None:
    # Both files share the same noise signal (mimicking real audio content),
    # but B has 1.0s of leading silence. Cross-correlation on noise has a
    # single clean peak — pure tones have side-lobes every period and aren't
    # representative of real podcast audio.
    rng = np.random.default_rng(seed=42)
    noise = (rng.standard_normal(int(3.0 * SR)) * 0.3).astype(np.float32)
    _write_wav(tmp_path / "a.wav", noise)
    _write_wav(tmp_path / "b.wav", np.concatenate([_silence(1.0), noise[: 2 * SR]]))
    groups = multicam.detect_groups(tmp_path)
    assert len(groups) == 1
    g = groups[0]
    assert len(g.clip_paths) == 2
    # One file is the anchor at offset 0.0; the other has the discovered offset.
    assert max(abs(o) for o in g.offsets_sec) == pytest.approx(1.0, abs=0.05)


@requires_ffmpeg
def test_diarize_by_mic_dominance_picks_louder_mic(tmp_path: Path) -> None:
    # Mic A: loud at [0, 1), silent at [1, 2). Mic B: opposite.
    a = np.concatenate([_tone(1.0, freq=440), _silence(1.0)])
    b = np.concatenate([_silence(1.0), _tone(1.0, freq=880)])
    _write_wav(tmp_path / "a.wav", a)
    _write_wav(tmp_path / "b.wav", b)
    group = MulticamGroup(
        group_id="g0",
        clip_paths=[tmp_path / "a.wav", tmp_path / "b.wav"],
        offsets_sec=[0.0, 0.0],
    )
    transcript = Transcript(
        source_path=tmp_path / "a.wav", source_hash="h", duration=2.0,
        segments=[
            Segment(text="loud A", start=0.0, end=1.0),
            Segment(text="loud B", start=1.0, end=2.0),
        ],
    )
    labels = multicam.diarize_by_mic_dominance(transcript, group)
    assert [l.speaker_idx for l in labels] == [0, 1]
    assert labels[0].speaker_label == "Speaker 1"
    assert labels[1].speaker_label == "Speaker 2"


def test_pick_angles_alternates_with_reaction_shots(tmp_path: Path) -> None:
    group = MulticamGroup(
        group_id="g0",
        clip_paths=[tmp_path / "a.mp4", tmp_path / "b.mp4"],
        offsets_sec=[0.0, 0.0],
    )
    transcript = Transcript(
        source_path=tmp_path / "a.mp4", source_hash="h", duration=60.0,
        segments=[Segment(text=f"line {i}", start=i * 5.0, end=(i + 1) * 5.0) for i in range(12)],
    )
    diarization = [
        SpeakerLabel(segment_idx=i, speaker_idx=0, speaker_label="Speaker 1",
                     rms_per_clip=[1.0, 0.1])
        for i in range(12)
    ]
    selections = multicam.pick_angles(
        transcript, diarization, group,
        reaction_interval_sec=20.0, reaction_hold_sec=2.0,
    )
    reasons = [s.reason for s in selections]
    assert "reaction" in reasons
    assert "primary" in reasons
    react_total = sum(s.timeline_out_sec - s.timeline_in_sec
                      for s in selections if s.reason == "reaction")
    assert react_total <= 8.0


def test_pick_angles_handles_single_speaker_group() -> None:
    group = MulticamGroup(
        group_id="g0",
        clip_paths=[Path("/tmp/a.mp4")],
        offsets_sec=[0.0],
    )
    transcript = Transcript(
        source_path=Path("/tmp/a.mp4"), source_hash="h", duration=10.0,
        segments=[Segment(text="solo", start=0.0, end=10.0)],
    )
    diarization = [SpeakerLabel(
        segment_idx=0, speaker_idx=0, speaker_label="Speaker 1",
        rms_per_clip=[1.0],
    )]
    selections = multicam.pick_angles(transcript, diarization, group)
    assert all(s.reason == "primary" for s in selections)
