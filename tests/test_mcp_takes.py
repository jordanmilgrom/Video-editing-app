"""Tests for cluster_takes_by_silence and align_takes_to_script MCP tools."""

from __future__ import annotations

from pathlib import Path

from roughcut_core.models import Transcript
from roughcut_mcp import tools


def _transcript_dict() -> dict:
    return {
        "source_path": "/tmp/x.wav",
        "source_hash": "h",
        "duration": 11.0,
        "language": "en",
        "segments": [
            {"text": "hello world", "start": 0.0, "end": 1.0, "words": []},
            {"text": "hello world", "start": 5.0, "end": 6.0, "words": []},
            {"text": "goodbye", "start": 10.0, "end": 11.0, "words": []},
        ],
    }


def test_cluster_takes_validates_transcript_shape() -> None:
    res = tools._cluster_takes_by_silence({"bogus": "data"}, 2.0)
    assert res.ok is False
    assert res.error == "invalid_spec"


def test_cluster_takes_groups_by_silence() -> None:
    res = tools._cluster_takes_by_silence(_transcript_dict(), 2.0)
    assert res.ok is True
    assert len(res.clusters) == 3  # all gaps > 2s
    assert res.clusters[0].in_sec == 0.0


def test_cluster_takes_accepts_transcript_model_dump() -> None:
    # The tool should accept exactly what `transcribe_video` returned.
    t = Transcript.model_validate(_transcript_dict())
    res = tools._cluster_takes_by_silence(t.model_dump(mode="json"), 2.0)
    assert res.ok is True


def test_align_takes_validates_transcript_shape() -> None:
    res = tools._align_takes_to_script({"x": 1}, "some script")
    assert res.ok is False and res.error == "invalid_spec"


def test_align_takes_returns_alignments() -> None:
    res = tools._align_takes_to_script(_transcript_dict(), "hello world\ngoodbye")
    assert res.ok is True
    assert len(res.alignments) == 2
    line0 = res.alignments[0]
    assert line0.line_text == "hello world"
    assert 0 in line0.candidate_segment_indices  # exact match seg 0
    assert 1 in line0.candidate_segment_indices  # exact match seg 1
