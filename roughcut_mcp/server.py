"""MCP stdio entrypoint.

stdout is reserved for the MCP protocol — every log line goes to stderr.
FastMCP's `.run()` defaults to stdio transport.

At startup we scan the jobs dir and mark any job whose worker pid is no
longer alive as `interrupted` — that's how a Claude Desktop quit-mid-
transcribe shows up in `list_jobs` the next time the agent connects.

v0.6.4 also wires in a stack-dump watchdog: a daemon thread that yells
into `cache/watchdog.log` if the event loop stalls for >5 s. Diagnostic
breadcrumbs for the next time the server feels stuck.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from roughcut_core import jobs
from roughcut_mcp.responses import cache_dir
from roughcut_mcp.tools import register_tools
from roughcut_mcp.watchdog import wire_into as wire_watchdog

SERVER_NAME = "roughcut"


def build_server() -> FastMCP:
    """Build and return a FastMCP server with all tools registered."""
    mcp = FastMCP(SERVER_NAME)
    register_tools(mcp)
    return mcp


def _configure_logging() -> None:
    level = os.getenv("ROUGHCUT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _recover_interrupted_jobs() -> None:
    """Mark any running-but-pidless jobs as interrupted at startup."""
    try:
        touched = jobs.recover_interrupted(cache_dir(None))
    except Exception:  # noqa: BLE001
        logging.getLogger("roughcut_mcp.server").exception("job recovery scan failed")
        return
    if touched:
        log = logging.getLogger("roughcut_mcp.server")
        log.info("marked %d interrupted job(s) from prior session: %s",
                 len(touched), ", ".join(j.job_id for j in touched))


def _start_watchdog() -> None:
    """Wire the stack-dump watchdog onto the running event loop, if any."""
    log_path = cache_dir(None) / "watchdog.log"
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # No loop running yet — FastMCP will create one. Defer to a
        # task scheduled at startup.
        return
    try:
        wire_watchdog(loop, log_path)
    except Exception:  # noqa: BLE001
        logging.getLogger("roughcut_mcp.server").exception("watchdog wiring failed")


def main() -> None:
    _configure_logging()
    _recover_interrupted_jobs()
    _start_watchdog()
    build_server().run()


if __name__ == "__main__":
    main()
