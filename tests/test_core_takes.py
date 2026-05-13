"""Tests for deterministic take primitives.

These functions do not call any LLM. Decision-making (best-take pick,
which alignment to trust) is the agent's responsibility.
"""

from __future__ import annotations

from pathlib import Path

from roughcut_core import takes
from roughcut_core.models import Segment, Transcript


def _seg(text: str, start: float, end: float) -> Segment:
    return Segment(text=text, start=start, end=end, words=[])


def _transcript(segments: list[Segment]) -> Transcript:
    return Transcript(
        source_path=Path("/tmp/x.wav"),
        source_hash="hash",
        duration=segments[-1].end if segments else 0.0,
        language="en",
        segments=segments,
    )


def test_split_variants_breaks_on_silence_gap() -> None:
    segs = [
        _seg("hello", 0.0, 1.0),
        _seg("world", 1.2, 2.0),
        _seg("again", 5.0, 6.0),  # gap = 3.0 > DEFAULT_SILENCE_THRESHOLD
    ]
    variants = takes._split_variants(segs)
    assert [len(v) for v in variants] == [2, 1]
    assert variants[0][0].text == "hello"
    assert variants[1][0].text == "again"


def test_best_line_matches_with_threshold() -> None:
    lines = ["hello world", "goodbye my friend"]
    assert takes._best_line("hello world", lines) == 0
    assert takes._best_line("goodbye my friend", lines) == 1
    assert takes._best_line("completely unrelated", lines) is None


def test_cluster_by_silence_groups_continuous_runs() -> None:
    segs = [
        _seg("hello", 0.0, 1.0),
        _seg("world", 1.2, 2.0),       # < 2s gap → same cluster
        _seg("retake", 5.0, 6.0),      # 3s gap → new cluster
        _seg("retake more", 6.1, 7.0), # < 2s gap → same cluster
    ]
    clusters = takes.cluster_by_silence(_transcript(segs))

    assert len(clusters) == 2
    assert clusters[0].in_sec == 0.0 and clusters[0].out_sec == 2.0
    assert clusters[0].text == "hello world"
    assert clusters[0].segment_indices == [0, 1]
    assert clusters[1].in_sec == 5.0 and clusters[1].out_sec == 7.0
    assert clusters[1].text == "retake retake more"
    assert clusters[1].segment_indices == [2, 3]


def test_cluster_by_silence_respects_custom_threshold() -> None:
    segs = [_seg("a", 0.0, 1.0), _seg("b", 1.5, 2.0)]
    # 0.5s gap → splits at threshold 0.4, but not at 0.6.
    assert len(takes.cluster_by_silence(_transcript(segs), 0.4)) == 2
    assert len(takes.cluster_by_silence(_transcript(segs), 0.6)) == 1


def test_cluster_by_silence_empty_transcript() -> None:
    empty = Transcript(source_path=Path("/x"), source_hash="h", duration=0.0, segments=[])
    assert takes.cluster_by_silence(empty) == []


def test_align_to_script_finds_candidates_per_line() -> None:
    segs = [
        _seg("hello world", 0.0, 1.0),
        _seg("hello world", 5.0, 6.0),     # second read of line 0
        _seg("goodbye my friend", 10.0, 11.0),
        _seg("totally unrelated", 20.0, 21.0),
    ]
    t = _transcript(segs)

    alignments = takes.align_to_script(t, "hello world\ngoodbye my friend")
    assert len(alignments) == 2

    line0 = alignments[0]
    assert line0.line_index == 0
    assert line0.line_text == "hello world"
    assert line0.candidate_segment_indices == [0, 1]  # both exact matches
    assert all(c == 1.0 for c in line0.confidence)

    line1 = alignments[1]
    assert line1.candidate_segment_indices == [2]
    assert line1.confidence[0] == 1.0


def test_align_to_script_sorts_candidates_by_confidence() -> None:
    segs = [
        _seg("hello world!!", 0.0, 1.0),   # close, not exact
        _seg("hello world", 5.0, 6.0),     # exact match → 1.0
    ]
    alignments = takes.align_to_script(_transcript(segs), "hello world")
    line0 = alignments[0]
    assert line0.candidate_segment_indices[0] == 1  # exact match first
    assert line0.confidence[0] >= line0.confidence[1]


def test_align_to_script_empty_script_returns_empty() -> None:
    t = _transcript([_seg("hello", 0.0, 1.0)])
    assert takes.align_to_script(t, "") == []
    assert takes.align_to_script(t, "   \n  \n") == []
