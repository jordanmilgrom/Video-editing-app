"""Async-job infrastructure for long-running roughcut tools.

Claude Desktop's tool-call timeout is shorter than a real transcription
of a 60-minute podcast. The fix is to return immediately with a job_id
and run the work in a detached subprocess that persists its progress to
disk. A second tool (`check_job_status`) polls the JSON; cancellation
SIGKILLs the pid; on server restart we scan for jobs whose pid is dead
and mark them `interrupted`.

Deterministic job ids mean a re-run with identical inputs is a free
cache hit: same `job_id = sha256(tool + abspath + size + mtime + model)`,
status `succeeded` → return the cached `result_path`.

State diagram:
                                            +------> succeeded
                                            |
  started ---> running -- subprocess work --+------> failed
                  ^                         |
                  |                         +------> cancelled (SIGKILLed)
                  |
                  +-------- server restart ------ interrupted
                  (pid no longer alive when server scanned at startup)

resume_job() flips an interrupted/failed job back to `started` and
spawns a fresh worker. Re-extracted intermediate state (e.g. cached
audio extract) is reused via the underlying transcribe cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

JobStatus = Literal[
    "started", "running", "succeeded", "failed", "interrupted", "cancelled"
]

# Tools we expose as async jobs. Anything not in this list stays synchronous
# in the MCP layer.
ASYNC_TOOLS = frozenset({
    "transcribe_video",
    "cluster_takes_by_silence",
    "align_takes_to_script",
    "detect_multicam_groups",
    "diarize_speakers",
    "pick_angle_per_segment",
})


class Job(BaseModel):
    job_id: str
    tool_name: str
    args: dict[str, Any]
    status: JobStatus = "started"
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
    """Deterministic id from tool + inputs. Re-running with identical
    inputs hits the same job folder and skips the work.

    For tools that take a path, we include the file's (size, mtime_ns)
    so editing the source invalidates the cache.
    """
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


def spawn(cache_dir: Path, tool_name: str, args: dict[str, Any]) -> Job:
    """Create a job and start its worker subprocess.

    Idempotent: if a `succeeded` job with the same id already exists,
    return it without spawning. If a `running` job exists with a live
    pid, also return that one. Anything else (failed / interrupted /
    cancelled) gets a fresh worker.
    """
    job_id = compute_job_id(tool_name, args)
    existing = read(cache_dir, job_id)
    if existing:
        if existing.status == "succeeded":
            return existing
        if existing.status == "running" and existing.pid and _alive(existing.pid):
            return existing

    now = time.time()
    job = Job(
        job_id=job_id, tool_name=tool_name, args=args,
        status="started", started_at=now, updated_at=now,
    )
    write(cache_dir, job)

    log_path = jobs_dir(cache_dir) / f"{job_id}.log"
    log_fh = open(log_path, "ab")
    env = os.environ.copy()
    env["ROUGHCUT_CACHE_DIR"] = str(cache_dir)
    # Detached: new session so a Claude Desktop quit doesn't take the
    # worker with it. stdin closed, stdout/stderr to a log file.
    proc = subprocess.Popen(
        [sys.executable, "-m", "roughcut_core.worker", job_id],
        env=env,
        stdin=subprocess.DEVNULL, stdout=log_fh, stderr=log_fh,
        start_new_session=True,
        close_fds=True,
    )
    job.pid = proc.pid
    write(cache_dir, job)
    return job


def cancel(cache_dir: Path, job_id: str) -> Job:
    """SIGTERM then (if still alive after 2s) SIGKILL. Updates status."""
    job = read(cache_dir, job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    if job.status in ("succeeded", "failed", "cancelled"):
        return job
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


def recover_interrupted(cache_dir: Path) -> list[Job]:
    """Mark every `running` job whose pid is no longer alive as `interrupted`.

    Called at server startup. Returns the list it touched, for logging.
    """
    touched: list[Job] = []
    for j in list_jobs(cache_dir):
        if j.status in ("started", "running") and (j.pid is None or not _alive(j.pid)):
            j.status = "interrupted"
            j.current_step = "interrupted (server restart / process death)"
            write(cache_dir, j)
            touched.append(j)
    return touched


def _alive(pid: int) -> bool:
    """True if `pid` is a live process owned by us. SIGNAL 0 doesn't kill."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
