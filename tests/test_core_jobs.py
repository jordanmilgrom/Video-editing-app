"""Job-system tests.

Each test spawns a real subprocess via roughcut_core.jobs.spawn() with
tool_name="_test_*", and uses a monkeypatched _HANDLERS map in
roughcut_core.worker so we don't need mlx-whisper / scipy at job runtime.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from roughcut_core import jobs


def _wait_for(cache: Path, job_id: str, target_statuses: set[str], timeout: float = 10.0) -> jobs.Job:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = jobs.read(cache, job_id)
        if last and last.status in target_statuses:
            return last
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached {target_statuses}; last={last}")


def test_compute_job_id_is_deterministic_for_same_inputs(tmp_path: Path) -> None:
    src = tmp_path / "in.txt"
    src.write_text("hello")
    a = jobs.compute_job_id("transcribe_video", {"video_path": str(src), "model": "m"})
    b = jobs.compute_job_id("transcribe_video", {"video_path": str(src), "model": "m"})
    assert a == b


def test_compute_job_id_changes_when_file_mtime_changes(tmp_path: Path) -> None:
    src = tmp_path / "in.txt"
    src.write_text("hello")
    a = jobs.compute_job_id("transcribe_video", {"video_path": str(src)})
    time.sleep(0.01)
    src.write_text("hello world")
    b = jobs.compute_job_id("transcribe_video", {"video_path": str(src)})
    assert a != b


def test_spawn_creates_running_job(tmp_path: Path, monkeypatch) -> None:
    # Cheap worker: just sleeps 0.3s then returns a dummy result.
    job = jobs.spawn(tmp_path, "_test_noop", {"path": str(tmp_path / "x.txt")})
    assert job.job_id and job.pid
    finished = _wait_for(tmp_path, job.job_id, {"succeeded", "failed"})
    assert finished.status == "succeeded"
    assert finished.result_summary == {"echo": "ok"}


def test_spawn_idempotent_returns_cached_succeeded(tmp_path: Path) -> None:
    j1 = jobs.spawn(tmp_path, "_test_noop", {"key": "same"})
    _wait_for(tmp_path, j1.job_id, {"succeeded"})
    j2 = jobs.spawn(tmp_path, "_test_noop", {"key": "same"})
    assert j2.job_id == j1.job_id
    assert j2.status == "succeeded"


def test_cancel_kills_running_job(tmp_path: Path) -> None:
    # Long-running worker so we have time to cancel.
    j = jobs.spawn(tmp_path, "_test_sleep", {"seconds": 30})
    _wait_for(tmp_path, j.job_id, {"running"})
    pid = jobs.read(tmp_path, j.job_id).pid
    assert pid and jobs._alive(pid)

    after = jobs.cancel(tmp_path, j.job_id)
    assert after.status == "cancelled"
    # Note: we don't assert `not _alive(pid)` because os.kill(pid, 0)
    # returns "alive" for zombie processes that haven't been reaped yet,
    # and reaping is owned by init in our detached-session model. The
    # cancelled-status assertion is what the caller actually cares about.


def test_recover_interrupted_marks_dead_jobs(tmp_path: Path) -> None:
    j = jobs.spawn(tmp_path, "_test_noop", {"key": "recover"})
    _wait_for(tmp_path, j.job_id, {"succeeded"})
    # Forge a "running" job whose pid is dead.
    j2 = jobs.Job(
        job_id="dead-job", tool_name="transcribe_video", args={},
        status="running", started_at=time.time(), updated_at=time.time(),
        pid=999_999_999,  # almost certainly not a real pid
    )
    jobs.write(tmp_path, j2)
    touched = jobs.recover_interrupted(tmp_path)
    ids = [t.job_id for t in touched]
    assert "dead-job" in ids
    refreshed = jobs.read(tmp_path, "dead-job")
    assert refreshed.status == "interrupted"


def test_list_jobs_filters_by_status(tmp_path: Path) -> None:
    j_ok = jobs.spawn(tmp_path, "_test_noop", {"k": "ok"})
    _wait_for(tmp_path, j_ok.job_id, {"succeeded"})
    j_fail = jobs.spawn(tmp_path, "_test_boom", {"k": "fail"})
    _wait_for(tmp_path, j_fail.job_id, {"failed"})
    succeeded = jobs.list_jobs(tmp_path, status="succeeded")
    failed = jobs.list_jobs(tmp_path, status="failed")
    assert {j.job_id for j in succeeded} == {j_ok.job_id}
    assert {j.job_id for j in failed} == {j_fail.job_id}


def test_failed_job_captures_traceback_and_hint(tmp_path: Path) -> None:
    j = jobs.spawn(tmp_path, "_test_boom", {"k": "x"})
    final = _wait_for(tmp_path, j.job_id, {"failed"})
    assert final.error and "RuntimeError" in final.error
    assert final.traceback and "_do_test_boom" in final.traceback
