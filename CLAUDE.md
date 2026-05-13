# roughcut — MCP-first AI rough-cut tool

A capability library + MCP server for AI-driven rough-cut editing. The
agent (Claude Desktop, Claude Code, or another MCP-compatible client)
drives reasoning by calling our tools. We expose deterministic video
capabilities; we do not embed an LLM.

For the live refactor plan and phase tracker, see `REFACTOR.md`.

## Architecture

```
            ┌─────────────────┐
            │ roughcut_mcp/   │   ← stdio server, tool wrappers
            └────────┬────────┘
                     │
                     ▼
            ┌─────────────────┐
            │ roughcut_core/  │   ← transcribe, takes, broll,
            │                 │     clips, fcpxml, models
            └─────────────────┘

  roughcut_core  has zero MCP imports — usable as a plain Python lib.
  roughcut_mcp   has zero ffmpeg/whisper imports — delegates to core.
```

## Module responsibilities

- **`roughcut_core/transcribe.py`** — ffmpeg audio extract +
  mlx-whisper, cached by `(abs_path, size, mtime_ns)` + model.
- **`roughcut_core/takes.py`** — `cluster_by_silence` and
  `align_to_script`. Pure functions; no LLM. Surfaces structural views
  of a transcript so the agent can pick takes.
- **`roughcut_core/broll.py`** — frame extraction + contact-sheet
  tiling with optional timecode overlay. No vision call here; the
  agent reads the sheet through the MCP image-content tool.
- **`roughcut_core/clips.py`** — ffprobe inventory; returns
  `ClipMeta` per video file.
- **`roughcut_core/fcpxml.py`** — FCPXML v1.10 emission. A-roll on
  the spine (V1), b-roll nested at `lane="1"` (V2). Times snap to
  rational frame boundaries.
- **`roughcut_core/models.py`** — Pydantic v2 contracts:
  `Transcript`/`Segment`/`Word`, `TakeCluster`, `ScriptAlignment`,
  `ClipMeta`. (`Take`/`Clip`/`BrollMatch`/`Sequence` retained until
  `fcpxml.py` is migrated to `SequenceSpec` in Phase C.)
- **`roughcut_mcp/server.py`** — stdio entrypoint. Logs to stderr;
  stdout reserved for the MCP protocol.
- **`roughcut_mcp/tools.py`** — tool registrations. Each tool
  validates inputs (absolute paths only), delegates to core, returns
  structured results or structured errors.

## Hard constraints

- Zero `anthropic` SDK references anywhere.
- Zero hardcoded LLM prompts in code — agents bring their own reasoning.
- Every tool description tells the agent **when** to use the tool, not
  just what it does.
- `roughcut_core/` has zero MCP imports.
- `roughcut_mcp/` has zero ffmpeg/whisper imports.
- Every module stays under 200 LOC.

## Caching

```
.roughcut-cache/
  transcripts/<safe_model>/<hash>.json
  broll-grids/<hash>.png            (added by Phase D when frame_grid lands)
```

Cache writes are atomic (tmp + rename). Cache keys are
`sha256(abs_path + size + mtime_ns)`.

## Out of scope (v0.2)

Music, color/audio adjustments, multicam, speaker diarization, GUI,
HTTP/REST wrapper, auth, billing, cloud anything.
