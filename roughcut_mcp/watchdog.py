"""Stack-dump watchdog for the MCP server's event loop.

Why: Jordan's v0.6.2 lockup (31 parallel transcribes) was invisible
to debug from the outside — Claude Desktop just stopped responding.
A watchdog that detects a stalled event loop and dumps every thread's
stack to disk gives us the breadcrumbs we'd need to diagnose the next
regression of that class.

Mechanism: an asyncio task bumps a heartbeat counter every 0.5s. A
plain daemon thread reads that counter every 1s; if it doesn't advance
for `stall_threshold` seconds, the thread writes `sys._current_frames()`
to `<cache_dir>/watchdog.log` and resets so we only get one dump per
stall.

The watchdog never blocks the server (read-only and append-only).
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import traceback
from pathlib import Path

STALL_THRESHOLD_SECONDS = 5.0
HEARTBEAT_INTERVAL_SECONDS = 0.5
WATCHER_POLL_SECONDS = 1.0


class Watchdog:
    def __init__(self, log_path: Path, stall_threshold: float = STALL_THRESHOLD_SECONDS) -> None:
        self.log_path = log_path
        self.stall_threshold = stall_threshold
        self._heartbeat = 0
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn the watcher thread. Caller schedules `heartbeat_forever`
        on the event loop separately (see `wire_into` below)."""
        if self._thread is not None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="roughcut-watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()

    async def heartbeat_forever(self) -> None:
        """Bump the heartbeat from the event loop. Cheap; runs forever."""
        while not self._stop_evt.is_set():
            self._heartbeat += 1
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                return

    def _loop(self) -> None:
        last_seen = self._heartbeat
        last_seen_at = time.time()
        already_dumped = False
        while not self._stop_evt.wait(WATCHER_POLL_SECONDS):
            current = self._heartbeat
            now = time.time()
            if current != last_seen:
                last_seen = current
                last_seen_at = now
                already_dumped = False
                continue
            stalled_for = now - last_seen_at
            if stalled_for >= self.stall_threshold and not already_dumped:
                self._dump_stacks(stalled_for)
                already_dumped = True

    def _dump_stacks(self, stalled_for: float) -> None:
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"\n===== watchdog: event-loop stalled "
                    f"{stalled_for:.1f}s at {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"(pid {os.getpid()}) =====\n"
                )
                for tid, frame in sys._current_frames().items():
                    f.write(f"--- Thread {tid} ---\n")
                    f.write("".join(traceback.format_stack(frame)))
                    f.write("\n")
        except OSError:
            # Watchdog can never crash the server.
            pass


def wire_into(loop: asyncio.AbstractEventLoop, log_path: Path) -> Watchdog:
    """Construct, start, and schedule a watchdog on the given event loop.

    The MCP server's `main()` calls this right before `mcp.run()`. The
    heartbeat task lives on the same loop the server uses; if that loop
    stalls (a sync I/O call inside an async tool, a C extension blocking
    the thread), the watcher thread notices and writes the stack dump.
    """
    wd = Watchdog(log_path)
    wd.start()
    loop.create_task(wd.heartbeat_forever())
    return wd
