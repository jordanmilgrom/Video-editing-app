# Refactor: MCP-first architecture

We are removing the `anthropic` SDK and all direct API calls from this
codebase. AI judgment (pick-the-cleanest-take, semantic b-roll matching)
leaves the project entirely; the user's chosen agent (Claude Desktop,
Claude Code, eventually others) drives reasoning by calling our MCP tools.

This document is the contract for the refactor.

## Why

- The agent's subscription pays for reasoning. We stop paying API tokens.
- Any MCP-compatible agent can drive this tool; we are no longer coupled
  to a specific Claude integration.
- Deterministic capabilities (ffmpeg / whisper / Pillow / FCPXML) are
  what this project is good at. Reasoning is what the agent is good at.
  The split is clean.

## Final module layout

```
roughcut_core/         pure capability library, no AI, no MCP
  __init__.py
  models.py            Pydantic v2 contracts
  transcribe.py        ffmpeg + mlx-whisper, cached
  takes.py             silence clustering + script alignment (deterministic)
  broll.py             frame grid extraction + tiling
  clips.py             ffprobe inventory
  fcpxml.py            FCPXML v1.10 emission

roughcut_mcp/          MCP server wrapper
  __init__.py
  server.py            stdio transport, stderr logging
  tools.py             thin wrappers that register tools to the server

tests/
  test_core_*.py       deterministic tests for each core module
  test_mcp_tools.py    tool-wiring + input validation

docs/
  agent-setup.md       Claude Desktop / Claude Code wiring
  example-workflow.md  reference orchestration the user pastes into chat
```

## Dependency direction

```
            ┌─────────────────┐
            │ roughcut_mcp/   │   ← server.py, tools.py
            └────────┬────────┘
                     │ (one-way)
                     ▼
            ┌─────────────────┐
            │ roughcut_core/  │   ← transcribe, takes, broll,
            │                 │     clips, fcpxml, models
            └─────────────────┘

  roughcut_core   has zero MCP imports.
  roughcut_mcp    has zero ffmpeg/whisper/Pillow imports — delegates to core.
```

## What moves

| From                        | To                                  |
| --------------------------- | ----------------------------------- |
| `roughcut/models.py`        | `roughcut_core/models.py`           |
| `roughcut/transcribe.py`    | `roughcut_core/transcribe.py`       |
| `roughcut/takes.py`         | `roughcut_core/takes.py` (slimmed)  |
| `roughcut/broll.py`         | `roughcut_core/broll.py` (slimmed)  |
| `roughcut/fcpxml.py`        | `roughcut_core/fcpxml.py`           |
| `tests/test_*.py`           | `tests/test_core_*.py`              |

## What gets deleted

- `roughcut/claude.py` — direct anthropic SDK wrapper.
- `roughcut/match.py` — AI-driven semantic matching is now the agent's job.
- `roughcut/cli.py`, `roughcut/__main__.py` — replaced by the MCP server.
- `roughcut/prompts/*.md` — content gets repurposed into `docs/example-workflow.md`.
- Claude-call code paths inside `takes.py` (best-take pick, claude
  clustering) and `broll.py` (clip vision description).
- `anthropic` package from `pyproject.toml` dependencies.
- `tests/test_claude.py`, `tests/test_match.py`.
- `IngestSpec` from `models.py` (no CLI to populate it).

## What gets added

| File                         | Purpose                                     |
| ---------------------------- | ------------------------------------------- |
| `roughcut_core/clips.py`     | ffprobe-based clip inventory                |
| `roughcut_mcp/server.py`     | MCP stdio server                            |
| `roughcut_mcp/tools.py`      | Tool registrations (thin core wrappers)     |
| `tests/test_core_clips.py`   | clips inventory tests                       |
| `tests/test_mcp_tools.py`    | MCP tool wiring + input validation          |
| `docs/agent-setup.md`        | Add server to Claude Desktop / Code         |
| `docs/example-workflow.md`   | Reference orchestration for the agent       |

New Pydantic models in `roughcut_core/models.py`:

- `TakeCluster` — output of `cluster_by_silence`.
- `ScriptAlignment` — output of `align_to_script`.
- `ClipMeta` — output of clips inventory.
- `SequenceSpec` — input to `generate_fcpxml` (lands in Phase C).

Existing `Take` / `Clip` / `BrollMatch` / `Sequence` models stay for now
because `fcpxml.py` still consumes them; they will be folded into
`SequenceSpec` in Phase C.

## MCP tools (final exposure)

| Tool                          | Inputs                                                  | Output                       |
| ----------------------------- | ------------------------------------------------------- | ---------------------------- |
| `transcribe_video`            | `video_path`, `language="auto"`, `model="whisper-large-v3-mlx"` | `Transcript` JSON  |
| `list_clips`                  | `folder`, `recursive=True`                              | `list[ClipMeta]`             |
| `cluster_takes_by_silence`    | `transcript_json`, `silence_threshold_sec=2.0`          | `list[TakeCluster]`          |
| `align_takes_to_script`       | `transcript_json`, `script_text`                        | `list[ScriptAlignment]`      |
| `extract_frame_grid`          | `video_path`, `num_frames=16`, `grid="4x4"`, `overlay_timecodes=True` | image + path  |
| `get_clip_thumbnail`          | `video_path`, `timecode_sec`                            | image content                |
| `generate_fcpxml`             | `sequence_spec`, `output_path`                          | output path + summary        |

## Phases

We build incrementally and stop after each phase for verification.

- **A. Restructure, no behavior change.** Move + slim core modules.
  Delete the AI integration layer. Run all existing deterministic tests;
  they pass. ← **starting here**
- **B. MCP skeleton.** Add the `mcp` SDK. Stand up `server.py` with one
  tool registered (`list_clips`). Hand back the exact Claude Desktop
  config snippet to wire it in.
- **C. Deterministic tools.** Add `transcribe_video`,
  `cluster_takes_by_silence`, `align_takes_to_script`,
  `generate_fcpxml`. `SequenceSpec` lands here.
- **D. Vision tools.** Add `extract_frame_grid` and
  `get_clip_thumbnail`; both return MCP image content.
- **E. Documentation.** README + `agent-setup.md` + `example-workflow.md`.

## Hard constraints

- Zero `anthropic` references anywhere in code or `pyproject.toml`.
- Each module under 200 LOC.
- `roughcut_core` is independently importable; importing it must not
  pull in `mcp` or any agent SDK.
- `roughcut_mcp` calls into `roughcut_core`; it does not duplicate logic.
- Paths exchanged across the MCP boundary are absolute. Relative paths
  are rejected at the tool boundary with a structured error.
- Tool descriptions are written for the agent (when to use it), not the
  human (what it does internally).
