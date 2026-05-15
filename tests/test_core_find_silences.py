"""Tests for find_silences — the take-tightening helper.

Synthesizes a transcript with known segment timings and asserts the
silence detector returns the right ranges and the right inverse
(speech_sub_segments) split.
"""

from __future__ import annotations

import json
from pathlib import Path

from roughcut_core import documentary
from roughcut_core.models import Segment, Transcript


def _write_transcript(tmp_path: Path, segments: list[Segment]) -> Path:
    src = tmp_path / "interview.mp4"
    src.touch()
    t = Transcript(
        source_path=src, source_hash="h",
        duration=segments[-1].end if segments else 0.0,
        language="en", segments=segments,
    )
    p = tmp_path / "transcript.json"
    p.write_text(t.model_dump_json())
    return p


def test_find_silences_detects_interior_pause(tmp_path: Path) -> None:
    """A 1.5s gap between segments inside the take is an interior silence."""
    segs = [
        Segment(text="hello", start=0.0, end=2.0),
        Segment(text="world", start=3.5, end=5.0),  # 1.5s gap
    ]
    p = _write_transcript(tmp_path, segs)
    result = documentary.find_silences(p, start_sec=0.0, end_sec=5.0,
                                       min_silence_sec=0.5)
    silences = result["silences"]
    assert len(silences) == 1
    s = silences[0]
    assert s["kind"] == "interior"
    assert s["start_sec"] == 2.0 and s["end_sec"] == 3.5
    assert s["duration_sec"] == 1.5

    # speech_sub_segments split around the silence: [0, 2] and [3.5, 5]
    subs = result["speech_sub_segments"]
    assert len(subs) == 2
    assert (subs[0]["source_in_sec"], subs[0]["source_out_sec"]) == (0.0, 2.0)
    assert (subs[1]["source_in_sec"], subs[1]["source_out_sec"]) == (3.5, 5.0)
    assert result["tightened_duration_sec"] == 3.5  # was 5.0 raw


def test_find_silences_ignores_short_pauses(tmp_path: Path) -> None:
    """Gaps shorter than min_silence_sec are not silences."""
    segs = [
        Segment(text="a", start=0.0, end=1.0),
        Segment(text="b", start=1.2, end=2.0),  # 0.2s gap
    ]
    p = _write_transcript(tmp_path, segs)
    result = documentary.find_silences(p, start_sec=0.0, end_sec=2.0,
                                       min_silence_sec=0.5)
    assert result["silences"] == []
    # speech_sub_segment is the entire range (no silence to split on)
    assert len(result["speech_sub_segments"]) == 1


def test_find_silences_tags_head_and_tail(tmp_path: Path) -> None:
    """Pre-roll and post-roll silences are tagged head / tail."""
    segs = [
        Segment(text="middle", start=2.0, end=4.0),
    ]
    p = _write_transcript(tmp_path, segs)
    result = documentary.find_silences(p, start_sec=0.0, end_sec=6.0,
                                       min_silence_sec=0.5)
    kinds = {s["kind"] for s in result["silences"]}
    assert kinds == {"head", "tail"}
    head = next(s for s in result["silences"] if s["kind"] == "head")
    tail = next(s for s in result["silences"] if s["kind"] == "tail")
    assert head["start_sec"] == 0.0 and head["end_sec"] == 2.0
    assert tail["start_sec"] == 4.0 and tail["end_sec"] == 6.0


def test_find_silences_returns_zero_silences_when_dense(tmp_path: Path) -> None:
    """Adjacent segments with no gap → no silences, original = tightened."""
    segs = [
        Segment(text="one", start=0.0, end=1.0),
        Segment(text="two", start=1.0, end=2.0),
        Segment(text="three", start=2.0, end=3.0),
    ]
    p = _write_transcript(tmp_path, segs)
    result = documentary.find_silences(p, start_sec=0.0, end_sec=3.0)
    assert result["silences"] == []
    assert result["original_duration_sec"] == result["tightened_duration_sec"] == 3.0
