"""Tests for the silence-clustering and script-alignment worker handlers.

In v0.6.0 these tools are async-jobs; the MCP wrapper just spawns a
subprocess. The deterministic logic lives in roughcut_core.worker's
handler functions, which take a `Job` + cache_dir and return
(result_path, summary). Calling those directly is faster and more
focused than driving the full subprocess lifecycle.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from roughcut_core import jobs, worker
from roughcut_core.models import Transcript
from roughcut_mcp import tools


def _transcript_dict() -> dict:
    return {
        "source_path": "/tmp/x.wav", "source_hash": "h", "duration": 11.0,
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


def _make_job(tmp_path: Path, tool_name: str, args: dict) -> jobs.Job:
    now = time.time()
    return jobs.Job(
        job_id="test", tool_name=tool_name, args=args,
        status="running", started_at=now, updated_at=now,
    )


def test_cluster_handler_writes_clusters_and_returns_summary(tmp_path: Path) -> None:
    tpath = _write_transcript(tmp_path)
    job = _make_job(tmp_path, "cluster_takes_by_silence", {
        "transcript_path": str(tpath), "silence_threshold_sec": 2.0,
    })
    result_path, summary = worker._do_cluster(job, tmp_path)
    assert summary["cluster_count"] == 3
    clusters = json.loads(Path(summary["clusters_path"]).read_text())
    assert clusters[0]["in_sec"] == 0.0


def test_align_handler_writes_alignments_and_match_counts(tmp_path: Path) -> None:
    tpath = _write_transcript(tmp_path)
    job = _make_job(tmp_path, "align_takes_to_script", {
        "transcript_path": str(tpath), "script_text": "hello world\ngoodbye",
    })
    result_path, summary = worker._do_align(job, tmp_path)
    assert summary["line_count"] == 2
    assert summary["lines_with_match"] == 2
    payload = json.loads(Path(summary["alignments_path"]).read_text())
    line0 = payload[0]
    assert 0 in line0["candidate_segment_indices"]
    assert 1 in line0["candidate_segment_indices"]


def test_mcp_wrapper_returns_job_id_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The thin MCP wrapper should validate inputs and return a job_id,
    not block on the worker subprocess."""
    monkeypatch.setenv("ROUGHCUT_CACHE_DIR", str(tmp_path / "cache"))
    tpath = _write_transcript(tmp_path)
    # Stub jobs.spawn so we don't actually launch a subprocess.
    captured = {}

    def fake_spawn(cache_dir, tool_name, args):
        captured["tool"] = tool_name
        captured["args"] = args
        return jobs.Job(
            job_id="stub", tool_name=tool_name, args=args,
            status="started", started_at=0.0, updated_at=0.0,
        )

    monkeypatch.setattr(tools.jobs, "spawn", fake_spawn)
    res = tools._spawn("cluster_takes_by_silence", {
        "transcript_path": str(tpath), "silence_threshold_sec": 2.0,
    })
    assert res.ok is True
    assert res.summary["job_id"] == "stub"
    assert res.summary["status"] == "started"
    assert "poll_with" in res.summary
    assert captured["tool"] == "cluster_takes_by_silence"
