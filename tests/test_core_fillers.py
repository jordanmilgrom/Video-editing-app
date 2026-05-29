"""Tests for filler-word detection.

Synthesizes a Transcript with embedded Word entries for fillers
("um", "you know") and asserts the detector returns the exact ranges
and aggregate stats.
"""

from __future__ import annotations

from pathlib import Path

from roughcut_core import fillers
from roughcut_core.models import Segment, Transcript, Word


def _write_transcript(tmp_path: Path, segments: list[Segment]) -> Path:
    src = tmp_path / "interview.mp4"
    src.touch()
    duration = segments[-1].end if segments else 0.0
    t = Transcript(
        source_path=src, source_hash="h", duration=duration,
        language="en", segments=segments,
    )
    p = tmp_path / "transcript.json"
    p.write_text(t.model_dump_json())
    return p


def _seg(text: str, start: float, end: float, *,
         words: list[tuple[str, float, float]]) -> Segment:
    return Segment(
        text=text, start=start, end=end,
        words=[Word(text=w, start=s, end=e) for w, s, e in words],
    )


def test_detect_single_word_um(tmp_path: Path) -> None:
    """A single 'um' word should be detected exactly."""
    seg = _seg("Um hello world.", 0.0, 3.0, words=[
        ("Um", 0.0, 0.3),
        ("hello", 0.4, 1.0),
        ("world.", 1.1, 3.0),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = fillers.detect_fillers(p)
    assert len(result["filler_ranges"]) == 1
    hit = result["filler_ranges"][0]
    assert hit["text"] == "Um"
    assert hit["pattern"] == "um"
    assert hit["start_sec"] == 0.0
    assert hit["end_sec"] == 0.3
    assert hit["duration_sec"] == 0.3
    assert result["total_filler_sec"] == 0.3


def test_detect_multi_word_you_know(tmp_path: Path) -> None:
    """A two-word 'you know' must match as a single range covering both words."""
    seg = _seg("I was you know thinking", 0.0, 3.0, words=[
        ("I", 0.0, 0.2),
        ("was", 0.2, 0.5),
        ("you", 0.6, 0.8),
        ("know", 0.85, 1.1),
        ("thinking", 1.2, 3.0),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = fillers.detect_fillers(p)
    hits = [h for h in result["filler_ranges"] if h["pattern"] == "you know"]
    assert len(hits) == 1
    h = hits[0]
    assert h["start_sec"] == 0.6
    assert h["end_sec"] == 1.1
    assert "you know" in h["text"].lower()
    assert h["duration_sec"] == 0.5


def test_punctuation_stripped(tmp_path: Path) -> None:
    """'Um,' (with comma) should still match the 'um' pattern."""
    seg = _seg("Um, okay.", 0.0, 1.5, words=[
        ("Um,", 0.0, 0.4),
        ("okay.", 0.5, 1.5),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = fillers.detect_fillers(p)
    assert len(result["filler_ranges"]) == 1
    assert result["filler_ranges"][0]["pattern"] == "um"


def test_whole_word_match_only(tmp_path: Path) -> None:
    """'umbrella' must NOT match 'um' — word-boundary respected."""
    seg = _seg("Umbrella is open.", 0.0, 2.0, words=[
        ("Umbrella", 0.0, 0.8),
        ("is", 0.9, 1.1),
        ("open.", 1.2, 2.0),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = fillers.detect_fillers(p)
    assert result["filler_ranges"] == []


def test_range_filter(tmp_path: Path) -> None:
    """`start_sec`/`end_sec` must clip out-of-range fillers."""
    seg = _seg("um one um two", 0.0, 5.0, words=[
        ("um", 0.0, 0.2),
        ("one", 0.3, 1.0),
        ("um", 3.0, 3.2),
        ("two", 3.3, 5.0),
    ])
    p = _write_transcript(tmp_path, [seg])
    # Only scan the second half.
    result = fillers.detect_fillers(p, start_sec=2.0, end_sec=5.0)
    assert len(result["filler_ranges"]) == 1
    assert result["filler_ranges"][0]["start_sec"] == 3.0


def test_multiple_fillers_aggregate_stats(tmp_path: Path) -> None:
    """3 ums + 1 'you know' → total_filler_sec and rate are exact."""
    seg = _seg("um one um two you know three um", 0.0, 60.0, words=[
        ("um", 0.0, 0.2),
        ("one", 0.3, 1.0),
        ("um", 2.0, 2.3),
        ("two", 2.4, 3.0),
        ("you", 10.0, 10.2),
        ("know", 10.25, 10.5),
        ("three", 11.0, 12.0),
        ("um", 55.0, 55.2),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = fillers.detect_fillers(p)
    # Total = 0.2 + 0.3 + 0.5 ('you know') + 0.2 = 1.2
    assert abs(result["total_filler_sec"] - 1.2) < 0.001
    # 4 hits over 60s = 4 fillers/min
    assert result["filler_words_per_minute"] == 4.0


def test_custom_patterns_extend_set(tmp_path: Path) -> None:
    """Caller-supplied patterns override the default."""
    seg = _seg("anyway hello", 0.0, 2.0, words=[
        ("anyway", 0.0, 0.5),
        ("hello", 0.6, 2.0),
    ])
    p = _write_transcript(tmp_path, [seg])
    # Default doesn't include 'anyway'.
    result = fillers.detect_fillers(p)
    assert result["filler_ranges"] == []
    # Custom override does.
    result2 = fillers.detect_fillers(p, patterns=("anyway",))
    assert len(result2["filler_ranges"]) == 1
    assert result2["filler_ranges"][0]["pattern"] == "anyway"


def test_segments_without_word_stamps_are_skipped(tmp_path: Path) -> None:
    """Whisper sometimes omits per-word stamps; skip those segments without
    crashing."""
    seg = _seg("um silently dropped", 0.0, 2.0, words=[])
    p = _write_transcript(tmp_path, [seg])
    result = fillers.detect_fillers(p)
    assert result["filler_ranges"] == []
    assert result["total_filler_sec"] == 0.0
