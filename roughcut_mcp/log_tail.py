"""Read recent log activity from cache_dir for self-diagnosis.

The server's stderr goes to Claude Desktop's log file (out of our
reach), but workers and the watchdog write into `cache_dir`:

    cache_dir/
      watchdog.log               # event-loop stalls
      jobs/worker-0.log          # per-slot stdout+stderr
      jobs/worker-1.log
      ...

`tail_recent` reads the trailing chunk of each, dates them by mtime,
and returns the last N lines combined. Agents that suspect something
is wrong can call `get_server_logs()` to see what the worker pool
was complaining about without asking the user to dig through logs.
"""

from __future__ import annotations

import os
from pathlib import Path


def _tail_bytes(path: Path, max_bytes: int = 32_768) -> str:
    """Read the last `max_bytes` of a file. Empty string on any failure."""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    offset = max(0, size - max_bytes)
    try:
        with open(path, "rb") as f:
            f.seek(offset, os.SEEK_SET)
            data = f.read()
    except OSError:
        return ""
    # If we seeked into the middle of a line, drop the leading partial.
    try:
        text = data.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return ""
    if offset > 0 and "\n" in text:
        text = text.split("\n", 1)[1]
    return text


def tail_recent(cache_dir: Path, tail: int = 100) -> dict:
    """Aggregate the last `tail` lines across watchdog + worker logs.

    Returns a dict the MCP tool layer can pass straight through:
      - log_files: list of {path, size_bytes, mtime, last_lines_count}
      - lines: merged tail, newest LAST (so it reads like a normal log)
      - tail_requested: int
    """
    logs: list[Path] = []
    cdir = Path(cache_dir)
    watchdog = cdir / "watchdog.log"
    if watchdog.is_file():
        logs.append(watchdog)
    jobs_dir = cdir / "jobs"
    if jobs_dir.is_dir():
        logs.extend(sorted(jobs_dir.glob("worker-*.log")))

    files_meta: list[dict] = []
    all_lines: list[tuple[float, str, str]] = []  # (mtime, source, line)
    for log_path in logs:
        try:
            mtime = log_path.stat().st_mtime
        except OSError:
            continue
        text = _tail_bytes(log_path)
        lines = [l for l in text.splitlines() if l.strip()]
        # Keep only the trailing `tail` from each file so a single noisy
        # log can't drown out the others. We re-trim after merging.
        for l in lines[-tail:]:
            all_lines.append((mtime, log_path.name, l))
        files_meta.append({
            "path": str(log_path),
            "size_bytes": log_path.stat().st_size,
            "mtime": round(mtime, 3),
            "lines_in_tail": len(lines[-tail:]),
        })

    # Sort by mtime; preserve in-file order for lines from the same file.
    all_lines.sort(key=lambda x: x[0])
    merged = [f"[{src}] {line}" for _, src, line in all_lines[-tail:]]

    return {
        "tail_requested": tail,
        "log_files": files_meta,
        "lines": merged,
        "hint": (
            None if merged else
            "No log entries found. Either nothing has run, or "
            "ROUGHCUT_CACHE_DIR points somewhere different than expected. "
            "Call get_project_paths() to confirm the cache_dir."
        ),
    }
