"""Tests for the documentary-mode helpers (read / search / summarize).

All pure-stdlib + roughcut_core.models; no heavy deps.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roughcut_core import documentary
from roughcut_core.models import Segment, Transcript


def _write(cache_dir: Path, source_video: Path, segments: list[Segment]) -> Path:
    """Drop a transcript JSON into cache_dir/transcripts/whatever/ to match
    the layout `transcribe_video` produces."""
    sub = cache_dir / "transcripts" / "test"
    sub.mkdir(parents=True, exist_ok=True)
    t = Transcript(
        source_path=source_video, source_hash="h",
        duration=segments[-1].end if segments else 0.0,
        language="en", segments=segments,
    )
    path = sub / f"{source_video.stem}.json"
    path.write_text(t.model_dump_json(), encoding="utf-8")
    return path


# ----- read_transcript ------------------------------------------------------


def test_read_transcript_returns_all_segments_when_within_budget(tmp_path: Path) -> None:
    src = tmp_path / "clip.mp4"
    src.touch()
    path = _write(tmp_path, src, [
        Segment(text="hello", start=0.0, end=1.0),
        Segment(text="world", start=1.0, end=2.0),
    ])
    out = documentary.read_transcript(path)
    assert out["total_segments"] == 2
    assert out["returned_range"] == [0, 2]
    assert out["has_more"] is False
    assert [s["text"] for s in out["segments"]] == ["hello", "world"]


def test_read_transcript_paginates_when_over_max_chars(tmp_path: Path) -> None:
    src = tmp_path / "long.mp4"
    src.touch()
    big = "x" * 500
    segments = [Segment(text=big, start=float(i), end=float(i + 1)) for i in range(10)]
    path = _write(tmp_path, src, segments)
    # Cap small enough to force pagination after ~3 segments.
    first = documentary.read_transcript(path, max_chars=1500)
    assert first["has_more"] is True
    assert first["next_start"] is not None and first["next_start"] > 0
    second = documentary.read_transcript(path, start_segment=first["next_start"], max_chars=1500)
    # Across the two pages we cover every segment with no overlap.
    seen = {s["idx"] for s in first["segments"]} | {s["idx"] for s in second["segments"]}
    # On enough pages we eventually finish.
    while second["has_more"]:
        second = documentary.read_transcript(
            path, start_segment=second["next_start"], max_chars=1500,
        )
        seen |= {s["idx"] for s in second["segments"]}
    assert seen == set(range(10))


def test_read_transcript_respects_explicit_end_segment(tmp_path: Path) -> None:
    src = tmp_path / "clip.mp4"
    src.touch()
    path = _write(tmp_path, src, [
        Segment(text=f"line {i}", start=float(i), end=float(i + 1)) for i in range(5)
    ])
    out = documentary.read_transcript(path, start_segment=1, end_segment=3)
    assert [s["idx"] for s in out["segments"]] == [1, 2]
    # The bounded request was satisfied, but segments 3 and 4 still exist
    # in the transcript — has_more reflects "more on disk", not "more in
    # the requested slice".
    assert out["has_more"] is True
    assert out["next_start"] == 3


# ----- search_transcripts ---------------------------------------------------


def test_search_transcripts_finds_substring_case_insensitive(tmp_path: Path) -> None:
    src = tmp_path / "interview.mp4"
    src.touch()
    _write(tmp_path, src, [
        Segment(text="We talked about Wisconsin cheese", start=0.0, end=2.0),
        Segment(text="The CHEESEHEADS were thrilled", start=2.0, end=4.0),
        Segment(text="Beer was also good", start=4.0, end=6.0),
    ])
    out = documentary.search_transcripts("cheese", tmp_path)
    assert out["result_count"] == 2
    hits = sorted(out["results"], key=lambda h: h["segment_idx"])
    assert hits[0]["matched_text"] == "We talked about Wisconsin cheese"
    assert hits[1]["matched_text"] == "The CHEESEHEADS were thrilled"


def test_search_transcripts_returns_context_segments(tmp_path: Path) -> None:
    src = tmp_path / "panel.mp4"
    src.touch()
    _write(tmp_path, src, [
        Segment(text="intro one", start=0.0, end=1.0),
        Segment(text="intro two", start=1.0, end=2.0),
        Segment(text="the keyword here", start=2.0, end=3.0),
        Segment(text="outro one", start=3.0, end=4.0),
        Segment(text="outro two", start=4.0, end=5.0),
    ])
    out = documentary.search_transcripts("keyword", tmp_path, context_segments=2)
    assert out["result_count"] == 1
    hit = out["results"][0]
    assert [s["text"] for s in hit["context_before"]] == ["intro one", "intro two"]
    assert [s["text"] for s in hit["context_after"]] == ["outro one", "outro two"]


def test_search_transcripts_scopes_to_folder(tmp_path: Path) -> None:
    in_folder = tmp_path / "interviews"
    in_folder.mkdir()
    elsewhere = tmp_path / "broll"
    elsewhere.mkdir()
    target = in_folder / "alice.mp4"
    target.touch()
    other = elsewhere / "bob.mp4"
    other.touch()
    _write(tmp_path, target, [Segment(text="rocket launch", start=0.0, end=1.0)])
    _write(tmp_path, other, [Segment(text="rocket launch", start=0.0, end=1.0)])
    scoped = documentary.search_transcripts("rocket", tmp_path, folder_path=in_folder)
    assert scoped["result_count"] == 1
    assert scoped["results"][0]["source_video"] == str(target)


def test_search_transcripts_empty_query_returns_no_results(tmp_path: Path) -> None:
    src = tmp_path / "x.mp4"
    src.touch()
    _write(tmp_path, src, [Segment(text="hello", start=0.0, end=1.0)])
    out = documentary.search_transcripts("", tmp_path)
    assert out["result_count"] == 0


# ----- summarize_clip -------------------------------------------------------


def test_summarize_clip_returns_snippets_and_longest_segment(tmp_path: Path) -> None:
    src = tmp_path / "interview.mp4"
    src.touch()
    segments = [
        Segment(text="Opening line about the company.", start=0.0, end=2.0),
        Segment(text="Short mid line.", start=2.0, end=3.0),
        Segment(text="A much longer monologue that goes on for a while " * 5,
                start=3.0, end=15.0),
        Segment(text="Closing remarks here.", start=15.0, end=17.0),
    ]
    path = _write(tmp_path, src, segments)
    out = documentary.summarize_clip(path)
    assert out["segment_count"] == 4
    assert out["filename"] == "interview.mp4"
    assert "Opening line" in out["opening_200_chars"]
    assert "Closing remarks" in out["closing_200_chars"]
    assert out["longest_continuous_segment_chars"] >= 200
    assert "monologue" in out["longest_segment_text"]
    assert out["duration_sec"] == 17.0
