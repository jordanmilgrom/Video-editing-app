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


def test_spawn_enqueues_and_worker_runs_to_success(tmp_path: Path) -> None:
    job = jobs.spawn(tmp_path, "_test_noop", {"path": str(tmp_path / "x.txt")})
    assert job.job_id
    assert job.status in ("queued", "started")
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
    j = jobs.spawn(tmp_path, "_test_sleep", {"seconds": 30})
    _wait_for(tmp_path, j.job_id, {"running"})
    pid = jobs.read(tmp_path, j.job_id).pid
    assert pid and jobs._alive(pid)
    after = jobs.cancel(tmp_path, j.job_id)
    assert after.status == "cancelled"


def test_recover_interrupted_marks_dead_jobs(tmp_path: Path) -> None:
    j = jobs.spawn(tmp_path, "_test_noop", {"key": "recover"})
    _wait_for(tmp_path, j.job_id, {"succeeded"})
    j2 = jobs.Job(
        job_id="dead-job", tool_name="transcribe_video", args={},
        status="running", started_at=time.time(), updated_at=time.time(),
        pid=999_999_999,
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


# ----- v0.6.3: queue + persistent-worker pool --------------------------------


def test_pop_from_queue_is_fifo(tmp_path: Path) -> None:
    jobs.jobs_dir(tmp_path)
    jobs._append_to_queue(tmp_path, "a")
    jobs._append_to_queue(tmp_path, "b")
    jobs._append_to_queue(tmp_path, "c")
    assert jobs.pop_from_queue(tmp_path) == "a"
    assert jobs.pop_from_queue(tmp_path) == "b"
    assert jobs.pop_from_queue(tmp_path) == "c"
    assert jobs.pop_from_queue(tmp_path) is None


def test_queue_depth_reflects_pending_work(tmp_path: Path) -> None:
    jobs.jobs_dir(tmp_path)
    assert jobs.queue_depth(tmp_path) == 0
    jobs._append_to_queue(tmp_path, "a")
    jobs._append_to_queue(tmp_path, "b")
    assert jobs.queue_depth(tmp_path) == 2
    jobs.pop_from_queue(tmp_path)
    assert jobs.queue_depth(tmp_path) == 1


def test_pool_size_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUGHCUT_WORKER_POOL_SIZE", "3")
    assert jobs.pool_size() == 3
    monkeypatch.setenv("ROUGHCUT_WORKER_POOL_SIZE", "0")
    assert jobs.pool_size() == 1
    monkeypatch.setenv("ROUGHCUT_WORKER_POOL_SIZE", "garbage")
    assert jobs.pool_size() == jobs.DEFAULT_POOL_SIZE


def test_spawn_burst_returns_quickly(tmp_path: Path) -> None:
    """v0.6.0..v0.6.2 spawned a subprocess per call — 31 parallel transcribe
    requests deadlocked the MCP server. v0.6.3 enqueues + ensures a
    single persistent worker. Each spawn() lands in milliseconds."""
    t0 = time.time()
    ids = [jobs.spawn(tmp_path, "_test_noop", {"i": i}).job_id for i in range(20)]
    elapsed = time.time() - t0
    assert len(set(ids)) == 20
    assert elapsed < 3.0, f"20 spawns took {elapsed:.2f}s"


def test_list_jobs_returns_quickly_under_burst(tmp_path: Path) -> None:
    """list_jobs must never IPC the worker — it reads JSON files only."""
    for i in range(20):
        jobs.spawn(tmp_path, "_test_noop", {"burst": i})
    t0 = time.time()
    listed = jobs.list_jobs(tmp_path)
    elapsed = time.time() - t0
    assert len(listed) >= 20
    assert elapsed < 1.0, f"list_jobs took {elapsed:.2f}s (target <1s)"


def test_cancel_queued_job_skips_worker(tmp_path: Path) -> None:
    busy = jobs.spawn(tmp_path, "_test_sleep", {"seconds": 5})
    _wait_for(tmp_path, busy.job_id, {"running"})
    queued = jobs.spawn(tmp_path, "_test_noop", {"will_be": "cancelled"})
    after = jobs.cancel(tmp_path, queued.job_id)
    assert after.status == "cancelled"
    jobs.cancel(tmp_path, busy.job_id)


def test_ensure_workers_spawns_pid_file(tmp_path: Path) -> None:
    jobs.jobs_dir(tmp_path)
    pids = jobs._ensure_workers(tmp_path)
    assert len(pids) == jobs.pool_size()
    pid_file = jobs._worker_pid_path(tmp_path, 0)
    assert pid_file.exists()
    assert int(pid_file.read_text().strip()) in pids
    import os, signal
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
