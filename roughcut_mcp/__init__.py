"""roughcut_mcp — MCP server wrapping roughcut_core capabilities.

This package must not import ffmpeg, mlx-whisper, or Pillow. All
heavy lifting is delegated to `roughcut_core`. Tool wrappers here only
validate inputs, call core, and shape responses.
"""

__version__ = "0.3.0"
