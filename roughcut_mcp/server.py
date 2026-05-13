"""MCP stdio entrypoint.

stdout is reserved for the MCP protocol — every log line goes to stderr.
FastMCP's `.run()` defaults to stdio transport.
"""

from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from roughcut_mcp.tools import register_tools

SERVER_NAME = "roughcut"


def build_server() -> FastMCP:
    """Build and return a FastMCP server with all tools registered.

    Separated from `main()` so tests can construct a server without
    starting the stdio loop.
    """
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


def main() -> None:
    _configure_logging()
    build_server().run()


if __name__ == "__main__":
    main()
