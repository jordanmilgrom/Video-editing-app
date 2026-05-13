"""Tests for cluster_takes_by_silence and align_takes_to_script MCP tools.

Both tools now take a `transcript_path` (a JSON file written by
transcribe_video) rather than an inline transcript dict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roughcut_core.models import Transcript
from roughcut_mcp import tools


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cdir = tmp_path / "cache"
    monkeypatch.setenv("ROUGHCUT_CACHE_DIR", str(cdir))
    return cdir


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


def _write_transcript(tmp_path: Path) -> Path:
    p = tmp_path / "t.json"
    p.write_text(Transcript.model_validate(_transcript_dict()).model_dump_json())
    return p


def test_cluster_takes_rejects_bad_path(isolated_cache: Path) -> None:
    res = tools._cluster_takes_by_silence("/no/such/file.json", 2.0)
    assert res.ok is False
    assert res.error in {"not_a_file", "relative_path"}


def test_cluster_takes_rejects_malformed_json(tmp_path: Path, isolated_cache: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{bogus}")
    res = tools._cluster_takes_by_silence(str(bad), 2.0)
    assert res.ok is False and res.error == "invalid_spec"


def test_cluster_takes_groups_by_silence(tmp_path: Path, isolated_cache: Path) -> None:
    tpath = _write_transcript(tmp_path)
    res = tools._cluster_takes_by_silence(str(tpath), 2.0)
    assert res.ok is True
    summary = res.summary or {}
    assert summary["cluster_count"] == 3  # all gaps > 2s
    clusters = json.loads(Path(summary["clusters_path"]).read_text())
    assert len(clusters) == 3
    assert clusters[0]["in_sec"] == 0.0


def test_align_takes_rejects_bad_path(isolated_cache: Path) -> None:
    res = tools._align_takes_to_script("/no/such/file.json", "x")
    assert res.ok is False


def test_align_takes_returns_summary_with_match_counts(
    tmp_path: Path, isolated_cache: Path
) -> None:
    tpath = _write_transcript(tmp_path)
    res = tools._align_takes_to_script(str(tpath), "hello world\ngoodbye")
    assert res.ok is True
    summary = res.summary or {}
    assert summary["line_count"] == 2
    assert summary["lines_with_match"] == 2
    payload = json.loads(Path(summary["alignments_path"]).read_text())
    assert len(payload) == 2
    line0 = payload[0]
    assert 0 in line0["candidate_segment_indices"]
    assert 1 in line0["candidate_segment_indices"]
