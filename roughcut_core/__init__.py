"""roughcut_core — pure, deterministic capabilities for AI rough-cut.

No LLM imports. No MCP imports. Just ffmpeg + mlx-whisper + Pillow +
FCPXML. Agents drive reasoning by calling the MCP wrappers in
`roughcut_mcp`, which delegate here.
"""

__version__ = "0.2.0"
