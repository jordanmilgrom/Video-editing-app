"""Tests for tighten_take — the fused editorial pass.

We synthesize a video+transcript pair where we know exactly what each
detector should fire on, then verify tighten_take fuses them correctly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roughcut_core import tighten
from roughcut_core.models import Segment, Transcript, Word
from tests.conftest import requires_ffmpeg


def _make_clip(tmp_path: Path, name: str, parts: list[tuple[str, float]]) -> Path:
    """Concatenate a sequence of (lavfi-input, duration) into one clip.

    Each part is generated separately with its own duration, then concat'd.
    """
    sub_paths: list[Path] = []
    for i, (input_spec, duration) in enumerate(parts):
        p = tmp_path / f"{name}_{i}.m4a"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", input_spec,
             "-t", str(duration),
             "-ac", "1", "-ar", "16000",
             "-c:a", "aac", str(p)],
            check=True,
        )
        sub_paths.append(p)
    list_file = tmp_path / f"{name}.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in sub_paths))
    out = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c", "copy", str(out)],
        check=True,
    )
    return out


def _write_transcript(tmp_path: Path, segments: list[Segment], duration: float) -> Path:
    src = tmp_path / "video.m4a"
    src.touch()
    t = Transcript(
        source_path=src, source_hash="h", duration=duration,
        language="en", segments=segments,
    )
    p = tmp_path / "transcript.json"
    p.write_text(t.model_dump_json())
    return p


@requires_ffmpeg
def test_tighten_take_combines_silences_and_fillers(tmp_path: Path) -> None:
    """5s take: 1s speech, 1s silence, 1s 'um' filler, 1s silence, 1s speech.
    Expect ~3s of skip and 2 tight sub-segments around the kept speech."""
    clip = _make_clip(tmp_path, "v.m4a", [
        ("sine=frequency=440", 1.0),
        ("anullsrc=r=16000:cl=mono", 1.0),
        ("sine=frequency=440", 1.0),     # the "um" — has energy, will be flagged by filler detector
        ("anullsrc=r=16000:cl=mono", 1.0),
        ("sine=frequency=440", 1.0),
    ])
    tr = _write_transcript(tmp_path, [
        Segment(text="hello", start=0.0, end=1.0, words=[
            Word(text="hello", start=0.0, end=1.0),
        ]),
        Segment(text="um", start=2.0, end=3.0, words=[
            Word(text="um", start=2.0, end=3.0),
        ]),
        Segment(text="world", start=4.0, end=5.0, words=[
            Word(text="world", start=4.0, end=5.0),
        ]),
    ], duration=5.0)

    result = tighten.tighten_take(clip, tr, start_sec=0.0, end_sec=5.0)

    # Should detect both silences (waveform) and the filler.
    assert result["stats"]["silence_sec_audio"] > 0
    assert result["stats"]["filler_sec"] == 1.0  # the "um" word
    # Tightened cut is significantly shorter than the original.
    assert result["stats"]["tightened_duration_sec"] < 3.0
    assert result["stats"]["time_saved_pct"] > 40
    # At least 2 tight sub-segments around the kept speech.
    assert result["stats"]["tight_segment_count"] >= 2
    # Every tight segment uses canonical field names.
    for seg in result["tight_segments"]:
        assert "source_in_sec" in seg
        assert "source_out_sec" in seg
        assert seg["source_out_sec"] > seg["source_in_sec"]


@requires_ffmpeg
def test_tighten_take_emits_canonical_sequence_spec_fields(tmp_path: Path) -> None:
    """tight_segments use source_in_sec / source_out_sec (drop-in for ARollSegment)."""
    clip = _make_clip(tmp_path, "v.m4a", [
        ("sine=frequency=440", 1.0),
        ("anullsrc=r=16000:cl=mono", 0.5),
        ("sine=frequency=440", 1.0),
    ])
    tr = _write_transcript(tmp_path, [
        Segment(text="hi", start=0.0, end=1.0, words=[Word(text="hi", start=0.0, end=1.0)]),
        Segment(text="there", start=1.5, end=2.5, words=[
            Word(text="there", start=1.5, end=2.5),
        ]),
    ], duration=2.5)
    result = tighten.tighten_take(clip, tr, start_sec=0.0, end_sec=2.5)
    assert result["tight_segments"]
    keys = set(result["tight_segments"][0].keys())
    assert {"source_in_sec", "source_out_sec", "duration_sec"}.issubset(keys)


@requires_ffmpeg
def test_tighten_take_min_segment_filter(tmp_path: Path) -> None:
    """min_segment_sec drops sub-segments that would be choppy."""
    # Three brief tones with silence between → tight sub-segments < 0.5s.
    clip = _make_clip(tmp_path, "v.m4a", [
        ("sine=frequency=440", 0.3),
        ("anullsrc=r=16000:cl=mono", 0.5),
        ("sine=frequency=440", 0.3),
        ("anullsrc=r=16000:cl=mono", 0.5),
        ("sine=frequency=440", 0.3),
    ])
    tr = _write_transcript(tmp_path, [
        Segment(text="a", start=0.0, end=0.3, words=[Word(text="a", start=0.0, end=0.3)]),
        Segment(text="b", start=0.8, end=1.1, words=[Word(text="b", start=0.8, end=1.1)]),
        Segment(text="c", start=1.6, end=1.9, words=[Word(text="c", start=1.6, end=1.9)]),
    ], duration=1.9)
    result = tighten.tighten_take(
        clip, tr, start_sec=0.0, end_sec=1.9, min_segment_sec=0.5,
    )
    # Every sub-segment under 0.5s should be dropped.
    for seg in result["tight_segments"]:
        assert seg["duration_sec"] >= 0.5


@requires_ffmpeg
def test_tighten_take_with_features_disabled(tmp_path: Path) -> None:
    """Disabling fillers / breaths / false_starts must zero those stats."""
    clip = _make_clip(tmp_path, "v.m4a", [
        ("sine=frequency=440", 1.0),
        ("anullsrc=r=16000:cl=mono", 0.5),
        ("sine=frequency=440", 1.0),
    ])
    tr = _write_transcript(tmp_path, [
        Segment(text="um hello", start=0.0, end=1.0, words=[
            Word(text="um", start=0.0, end=0.3),
            Word(text="hello", start=0.4, end=1.0),
        ]),
        Segment(text="world", start=1.5, end=2.5, words=[
            Word(text="world", start=1.5, end=2.5),
        ]),
    ], duration=2.5)
    result = tighten.tighten_take(
        clip, tr, start_sec=0.0, end_sec=2.5,
        include_fillers=False, include_breaths=False, include_false_starts=False,
    )
    assert result["stats"]["filler_sec"] == 0.0
    assert result["stats"]["breath_sec"] == 0.0
    assert result["stats"]["false_start_sec"] == 0.0
    # Audio silence is still detected.
    assert result["stats"]["silence_sec_audio"] > 0


def test_tighten_take_rejects_inverted_range(tmp_path: Path) -> None:
    """end_sec <= start_sec must raise immediately."""
    clip = tmp_path / "fake.m4a"; clip.touch()
    tr = _write_transcript(tmp_path, [], duration=0.0)
    with pytest.raises(ValueError, match="must be > start_sec"):
        tighten.tighten_take(clip, tr, start_sec=2.0, end_sec=1.0)


def test_merge_overlapping_unions_ranges() -> None:
    """Direct unit test of the merge helper."""
    labeled = [
        (1.0, 2.0, "silence"),
        (1.5, 3.0, "filler"),    # overlaps the first
        (5.0, 6.0, "silence"),
    ]
    merged = tighten._merge_overlapping(labeled, 0.0, 10.0)
    assert len(merged) == 2
    assert merged[0]["start_sec"] == 1.0 and merged[0]["end_sec"] == 3.0
    assert set(merged[0]["sources"]) == {"silence", "filler"}
    assert merged[1]["start_sec"] == 5.0 and merged[1]["end_sec"] == 6.0


def test_invert_drops_short_segments() -> None:
    """_invert drops tight segments shorter than min_segment_sec."""
    skipped = [
        {"start_sec": 1.0, "end_sec": 1.2, "duration_sec": 0.2, "sources": ["x"]},
    ]
    # The post-skip tail [1.2, 5.0] is 3.8s — kept.
    # The pre-skip head [0.0, 1.0] is 1.0s — kept.
    tight = tighten._invert(skipped, 0.0, 5.0, min_segment_sec=0.5)
    assert len(tight) == 2
    # The same setup with min_segment_sec=1.5 drops the head.
    tight2 = tighten._invert(skipped, 0.0, 5.0, min_segment_sec=1.5)
    assert len(tight2) == 1
    assert tight2[0]["source_in_sec"] == 1.2
