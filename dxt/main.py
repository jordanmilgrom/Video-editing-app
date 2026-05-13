"""DXT entry point.

Claude Desktop launches this script via `python3 server/main.py`. We set
PYTHONPATH in the manifest so `roughcut_mcp` and its vendored deps under
`server/lib/` are importable.
"""

from roughcut_mcp.server import main

if __name__ == "__main__":
    main()
