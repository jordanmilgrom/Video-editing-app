"""Tests for false-start detection (word-stream pattern matching)."""

from __future__ import annotations

from pathlib import Path

from roughcut_core import false_starts
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


def _seg(start: float, end: float,
         words: list[tuple[str, float, float]]) -> Segment:
    return Segment(
        text=" ".join(w for w, _, _ in words),
        start=start, end=end,
        words=[Word(text=w, start=s, end=e) for w, s, e in words],
    )


def test_single_word_repeat(tmp_path: Path) -> None:
    """'I I think' should be flagged with the second 'I' kept."""
    seg = _seg(0.0, 3.0, [
        ("I", 0.0, 0.2),
        ("I", 0.4, 0.6),
        ("think", 0.7, 1.2),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = false_starts.detect_false_starts(p)
    assert len(result["false_starts"]) == 1
    r = result["false_starts"][0]
    assert r["pattern"] in ("single_word_repeat", "single_word_stutter")
    assert r["occurrences"] == 2
    assert r["kept_at_sec"] == 0.4
    assert r["start_sec"] == 0.0
    # The skip range covers up to (but not including) the kept occurrence.
    assert r["end_sec"] == 0.4


def test_single_word_stutter_three_times(tmp_path: Path) -> None:
    """'I I I think' is a stutter (3+ repeats)."""
    seg = _seg(0.0, 3.0, [
        ("I", 0.0, 0.15),
        ("I", 0.3, 0.45),
        ("I", 0.6, 0.75),
        ("think", 1.0, 1.5),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = false_starts.detect_false_starts(p)
    assert len(result["false_starts"]) == 1
    r = result["false_starts"][0]
    assert r["pattern"] == "single_word_stutter"
    assert r["occurrences"] == 3
    assert r["kept_at_sec"] == 0.6


def test_phrase_repeat(tmp_path: Path) -> None:
    """'the thing the thing is' → flag the first 'the thing'."""
    seg = _seg(0.0, 3.0, [
        ("the", 0.0, 0.2),
        ("thing", 0.25, 0.5),
        ("the", 0.8, 1.0),
        ("thing", 1.05, 1.3),
        ("is", 1.4, 1.6),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = false_starts.detect_false_starts(p)
    repeat_hits = [r for r in result["false_starts"] if r["pattern"] == "phrase_repeat"]
    assert len(repeat_hits) == 1
    r = repeat_hits[0]
    assert r["kept_at_sec"] == 0.8


def test_no_repeats_returns_empty(tmp_path: Path) -> None:
    """A clean utterance has no false starts."""
    seg = _seg(0.0, 3.0, [
        ("Hello", 0.0, 0.5),
        ("world", 0.6, 1.0),
        ("today", 1.1, 1.5),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = false_starts.detect_false_starts(p)
    assert result["false_starts"] == []
    assert result["total_false_start_sec"] == 0.0


def test_gap_too_large_breaks_repeat(tmp_path: Path) -> None:
    """If the second 'I' is more than max_gap_sec after the first, it's not a repeat."""
    seg = _seg(0.0, 10.0, [
        ("I", 0.0, 0.2),
        ("I", 5.0, 5.2),  # 4.8s gap → not a repeat with default max_gap_sec=2.0
        ("think", 6.0, 6.5),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = false_starts.detect_false_starts(p)
    assert result["false_starts"] == []


def test_range_filter_clips_out_of_range(tmp_path: Path) -> None:
    """A false start before `start_sec` should be excluded."""
    seg = _seg(0.0, 10.0, [
        ("I", 0.0, 0.2),
        ("I", 0.5, 0.7),
        ("think", 0.8, 1.2),
        ("the", 5.0, 5.2),
        ("the", 5.4, 5.6),
        ("plan", 5.7, 6.0),
    ])
    p = _write_transcript(tmp_path, [seg])
    # Only scan from 3s onwards. Should only catch the second false start.
    result = false_starts.detect_false_starts(p, start_sec=3.0, end_sec=8.0)
    assert len(result["false_starts"]) == 1
    assert result["false_starts"][0]["kept_at_sec"] == 5.4


def test_case_insensitive_match(tmp_path: Path) -> None:
    """'I I' should match even when capitalization or punctuation differs."""
    seg = _seg(0.0, 3.0, [
        ("I,", 0.0, 0.2),
        ("I", 0.4, 0.6),
        ("think", 0.7, 1.2),
    ])
    p = _write_transcript(tmp_path, [seg])
    result = false_starts.detect_false_starts(p)
    assert len(result["false_starts"]) == 1
