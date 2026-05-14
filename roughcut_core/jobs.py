"""Async-job infrastructure: queue + persistent worker pool.

v0.6.3 redesign. The v0.6.0 model was "one subprocess per job." It
deadlocked Claude Desktop when 31 transcribes fired in parallel:
31 mlx-whisper Python processes loading ~150 MB each + GPU contention.
v0.6.3 introduces:

- A single persistent worker subprocess per cache dir (configurable
  pool size via ROUGHCUT_WORKER_POOL_SIZE; default 1, which is
  correct for mlx-whisper since the Metal GPU doesn't parallelize
  whisper instances usefully).
- An on-disk queue (`cache/jobs/queue.txt`, fcntl-locked).
- The MCP server's "spawn a job" path is now: write job record +
  append job_id to queue + ensure worker is running. No subprocess
  Popen on the hot path. `check_job_status` / `list_jobs` / `cancel`
  only read/write JSON files — never IPC with the worker.

State diagram (v0.6.3):

   queued  →  running  →  succeeded
      |                    failed
      |                    cancelled (SIGTERM)
      |
      +-- server restart → interrupted (worker pid no longer alive)

Backward compat: `started` (v0.6.0..v0.6.2) is treated as `queued`.
Re-using `started` keeps existing on-disk job records readable.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

JobStatus = Literal[
    "started", "queued", "running", "succeeded",
    "failed", "interrupted", "cancelled",
]

ASYNC_TOOLS = frozenset({
    "transcribe_video",
    "prewarm_model",
    "cluster_takes_by_silence",
    "align_takes_to_script",
    "detect_multicam_groups",
    "diarize_speakers",
    "pick_angle_per_segment",
})

# Concurrency cap for the persistent worker pool. Default 4: empirically
# strikes the right balance on M-series Macs for the whisper-small /
# whisper-medium models. Beyond 4, GPU contention dominates and the
# average per-job time goes up. v0.6.3 shipped with default 1 — too
# conservative for editors firing batch transcriptions across 30+ clips.
# Power users tune via ROUGHCUT_WORKER_POOL_SIZE.
DEFAULT_POOL_SIZE = 4


def pool_size() -> int:
    try:
        n = int(os.environ.get("ROUGHCUT_WORKER_POOL_SIZE", "") or DEFAULT_POOL_SIZE)
        return max(1, n)
    except ValueError:
        return DEFAULT_POOL_SIZE


class Job(BaseModel):
    job_id: str
    tool_name: str
    args: dict[str, Any]
    status: JobStatus = "queued"
    progress_pct: float | None = None
    current_step: str | None = None
    started_at: float
    updated_at: float
    eta_seconds: float | None = None
    pid: int | None = None
    result_path: str | None = None
    result_summary: dict | None = None
    error: str | None = None
    traceback: str | None = None
    hint: str | None = None


def compute_job_id(tool_name: str, args: dict[str, Any]) -> str:
    parts: list[str] = [tool_name]
    for k in sorted(args):
        v = args[k]
        if isinstance(v, (str, Path)):
            p = Path(v)
            if p.is_file():
                st = p.stat()
                parts.append(f"{k}={p.resolve()}|{st.st_size}|{st.st_mtime_ns}")
                continue
            if p.is_dir():
                st = p.stat()
                parts.append(f"{k}={p.resolve()}|dir|{st.st_mtime_ns}")
                continue
        parts.append(f"{k}={v!r}")
    digest = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    return digest[:16]


def jobs_dir(cache_dir: Path) -> Path:
    d = cache_dir / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_for(cache_dir: Path, job_id: str) -> Path:
    return jobs_dir(cache_dir) / f"{job_id}.json"


def queue_path(cache_dir: Path) -> Path:
    return jobs_dir(cache_dir) / "queue.txt"


def _queue_lock_path(cache_dir: Path) -> Path:
    return jobs_dir(cache_dir) / "queue.lock"


def _worker_pid_path(cache_dir: Path, slot: int = 0) -> Path:
    return jobs_dir(cache_dir) / f"worker-{slot}.pid"


def read(cache_dir: Path, job_id: str) -> Job | None:
    p = path_for(cache_dir, job_id)
    if not p.is_file():
        return None
    return Job.model_validate_json(p.read_text(encoding="utf-8"))


def write(cache_dir: Path, job: Job) -> None:
    """Atomic write — workers update progress concurrently with the server."""
    job.updated_at = time.time()
    p = path_for(cache_dir, job.job_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(job.model_dump_json(), encoding="utf-8")
    os.replace(tmp, p)


def list_jobs(cache_dir: Path, status: JobStatus | None = None) -> list[Job]:
    out: list[Job] = []
    for p in sorted(jobs_dir(cache_dir).glob("*.json"), key=lambda x: -x.stat().st_mtime):
        try:
            j = Job.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if status is None or j.status == status:
            out.append(j)
    return out


# ---------------------------------------------------------------------------
# Enqueue + worker management (hot path)
# ---------------------------------------------------------------------------


def spawn(cache_dir: Path, tool_name: str, args: dict[str, Any]) -> Job:
    """Enqueue a job. Fast — never blocks on subprocess startup.

    Idempotent: if a job with the same id is already `succeeded`,
    `queued`, or live-`running`, we return that record without
    re-enqueueing.
    """
    job_id = compute_job_id(tool_name, args)
    existing = read(cache_dir, job_id)
    if existing:
        if existing.status == "succeeded":
            return existing
        if existing.status in ("queued", "started"):
            _ensure_workers(cache_dir)
            return existing
        if existing.status == "running" and existing.pid and _alive(existing.pid):
            return existing

    now = time.time()
    job = Job(
        job_id=job_id, tool_name=tool_name, args=args,
        status="queued", started_at=now, updated_at=now,
    )
    write(cache_dir, job)
    _append_to_queue(cache_dir, job_id)
    _ensure_workers(cache_dir)
    return job


def _append_to_queue(cache_dir: Path, job_id: str) -> None:
    q = queue_path(cache_dir)
    with _filelock(_queue_lock_path(cache_dir)):
        # Use a flat newline-delimited list. The worker pops from the head.
        with open(q, "a", encoding="utf-8") as f:
            f.write(f"{job_id}\n")


def pop_from_queue(cache_dir: Path) -> str | None:
    """Atomic pop of the next job_id, or None if queue is empty."""
    q = queue_path(cache_dir)
    with _filelock(_queue_lock_path(cache_dir)):
        if not q.exists():
            return None
        lines = q.read_text(encoding="utf-8").splitlines()
        # Skip blanks.
        lines = [l for l in lines if l.strip()]
        if not lines:
            q.unlink(missing_ok=True)
            return None
        first, rest = lines[0], lines[1:]
        if rest:
            tmp = q.with_suffix(".txt.tmp")
            tmp.write_text("\n".join(rest) + "\n", encoding="utf-8")
            os.replace(tmp, q)
        else:
            q.unlink(missing_ok=True)
        return first


def queue_depth(cache_dir: Path) -> int:
    q = queue_path(cache_dir)
    if not q.exists():
        return 0
    return sum(1 for line in q.read_text(encoding="utf-8").splitlines() if line.strip())


def _ensure_workers(cache_dir: Path) -> list[int]:
    """Make sure the configured pool of workers is alive. Returns live pids.

    Cheap to call repeatedly: if every slot's pid is alive, this is a
    handful of `os.kill(pid, 0)` checks. New workers only spawn when a
    slot has no live process.
    """
    live: list[int] = []
    for slot in range(pool_size()):
        pid_file = _worker_pid_path(cache_dir, slot)
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                if _alive(pid):
                    live.append(pid)
                    continue
            except (ValueError, OSError):
                pass
        new_pid = _spawn_worker(cache_dir, slot)
        live.append(new_pid)
    return live


def _spawn_worker(cache_dir: Path, slot: int) -> int:
    """Spawn a detached worker subprocess and return its pid."""
    log_path = jobs_dir(cache_dir) / f"worker-{slot}.log"
    log_fh = open(log_path, "ab")
    env = os.environ.copy()
    env["ROUGHCUT_CACHE_DIR"] = str(cache_dir)
    env["ROUGHCUT_WORKER_SLOT"] = str(slot)
    proc = subprocess.Popen(
        [sys.executable, "-m", "roughcut_core.worker"],
        env=env,
        stdin=subprocess.DEVNULL, stdout=log_fh, stderr=log_fh,
        start_new_session=True,
        close_fds=True,
    )
    _worker_pid_path(cache_dir, slot).write_text(str(proc.pid))
    return proc.pid


# ---------------------------------------------------------------------------
# Cancel + recovery
# ---------------------------------------------------------------------------


def cancel(cache_dir: Path, job_id: str) -> Job:
    """Cancel a job.

    - If queued: just flip status. Worker skips on pop.
    - If running: SIGTERM the worker (it owns this job exclusively
      under pool_size=1). The worker's signal handler updates the job
      to "cancelled" and exits; the next `_ensure_workers` call
      respawns a fresh worker to drain the rest of the queue.
    - If already terminal: no-op.
    """
    job = read(cache_dir, job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    if job.status in ("succeeded", "failed", "cancelled"):
        return job

    if job.status in ("queued", "started"):
        job.status = "cancelled"
        job.current_step = "cancelled (was queued)"
        write(cache_dir, job)
        return job

    # status == "running": signal the worker that owns this job.
    if job.pid and _alive(job.pid):
        try:
            os.killpg(os.getpgid(job.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        for _ in range(20):
            if not _alive(job.pid):
                break
            time.sleep(0.1)
        else:
            try:
                os.killpg(os.getpgid(job.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    job.status = "cancelled"
    job.current_step = "cancelled"
    write(cache_dir, job)
    return job


def restart_workers(cache_dir: Path) -> dict:
    """Kill every live worker, drop pid + queue lock files, respawn the pool.

    Self-heal for when the MCP server reports workers alive but the user
    can't get a job through. `cancel_job` only signals one worker at a
    time; this is the heavier hammer the `restart_workers` MCP tool wraps.
    """
    killed: list[int] = []
    for slot in range(pool_size()):
        pid_file = _worker_pid_path(cache_dir, slot)
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                if _alive(pid):
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGTERM)
                        killed.append(pid)
                    except (ProcessLookupError, PermissionError):
                        pass
            except (ValueError, OSError):
                pass
            pid_file.unlink(missing_ok=True)
    # Any "running" jobs whose worker just died need to be flipped before
    # we respawn, otherwise check_job_status reports stale "running" forever.
    touched = recover_interrupted(cache_dir)
    fresh_pids = _ensure_workers(cache_dir)
    return {
        "killed_pids": killed,
        "interrupted_jobs": [j.job_id for j in touched],
        "new_pids": fresh_pids,
        "queue_depth": queue_depth(cache_dir),
    }


def recover_interrupted(cache_dir: Path) -> list[Job]:
    """At server startup, reconcile records with reality.

    Jobs marked `running` whose pid is dead → `interrupted`.
    Jobs marked `queued` whose worker is gone get re-spawned via
    `_ensure_workers` if anything is left in the queue.
    """
    touched: list[Job] = []
    for j in list_jobs(cache_dir):
        if j.status in ("started", "running") and (j.pid is None or not _alive(j.pid)):
            j.status = "interrupted"
            j.current_step = "interrupted (server restart / process death)"
            write(cache_dir, j)
            touched.append(j)
    # If the queue has work, make sure the pool is alive.
    if queue_depth(cache_dir) > 0:
        _ensure_workers(cache_dir)
    return touched


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def _filelock(path: Path):
    """fcntl-based exclusive lock, scoped to a `with` block.

    Cheap on macOS / Linux. The lock file is created on first use and
    persists between calls — only the OS-level lock is short-lived.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
