# roughcut — AI rough-cut tool for video editors

A Python CLI that ingests a folder of interview footage and a folder of b-roll
clips and emits an FCPXML rough cut that imports cleanly into Premiere Pro and
DaVinci Resolve. A-roll lives on V1, selected b-roll on V2, clips relink to
source by absolute path.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ interview/   │     │ broll/       │     │ script.txt?  │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       ▼                    ▼                    │
 transcribe.py          broll.py                 │
 (mlx-whisper)      (ffmpeg frame grid           │
       │             + Claude vision)            │
       │                    │                    │
       ▼                    │                    │
   takes.py  ◀──────────────┼────────────────────┘
 (silence split,            │
  Claude pick best)         │
       │                    │
       └─────────┬──────────┘
                 ▼
            match.py
       (Claude semantic match
        sentence → b-roll)
                 │
                 ▼
            fcpxml.py
       (V1 A-roll, V2 b-roll,
        absolute paths)
                 │
                 ▼
           cut.fcpxml
```

Every module is independently testable, < 200 LOC, and writes its outputs to
a cache directory keyed by content hash so reruns are cheap.

## Data flow

1. **CLI parses args** → `cli.py` builds an `IngestSpec` (paths to interview
   folder, b-roll folder, optional script, output path, cache dir).
2. **Transcription** → for each interview file: extract audio with ffmpeg to
   16 kHz mono WAV, run mlx-whisper with `word_timestamps=True`, persist
   `Transcript` (segments + words with start/end seconds) to
   `.roughcut-cache/transcripts/<hash>.json`.
3. **Take detection** → `takes.py` consumes transcripts. Two strategies:
   - **With script**: align spoken text against script lines (fuzzy match)
     and group consecutive matches to the same line as repeat takes.
   - **No script**: detect silence gaps > 2.0 s as take boundaries, then
     ask Claude to cluster groups that read the same content.
4. **Pick best take** → for each take cluster, send Claude variants (text +
   timecodes), tool-use call returns chosen `Take` (clip id, in/out, reason).
5. **B-roll analysis** → for each broll clip: pull 16 frames evenly spaced,
   tile into a 4×4 grid (Pillow), overlay each cell with its source timecode,
   send PNG to Claude vision. Save structured `Clip` (subject, motion, mood,
   tags, suggested_in/out) to `.roughcut-cache/broll/<hash>.json`.
6. **Match** → split chosen A-roll transcript into sentences. For each
   sentence, Claude picks 0–3 inserts from the b-roll library by semantic
   relevance and returns `BrollMatch` (clip_id, in, out, sentence_index).
7. **FCPXML** → `fcpxml.py` builds an FCPXML v1.10 doc:
   - `<resources>`: one `<asset>` per unique source file (absolute path).
   - `<library>` → `<event>` → `<project>` → `<sequence>` → `<spine>`.
   - A-roll: contiguous `<asset-clip>` on lane 0 (V1).
   - B-roll: connected `<asset-clip>` on lane 1 (V2) at matched offsets.
   - Frame rate snapped to A-roll source.

## Module-by-module build order

We build incrementally. **Stop after each module and wait for verification.**

1. **`models.py`** — Pydantic v2 types: `Word`, `Segment`, `Transcript`,
   `Take`, `Clip`, `BrollMatch`, `Sequence`, `IngestSpec`. Defines the
   contract every other module depends on.
2. **`claude.py`** — anthropic SDK wrapper. Loads prompts from
   `roughcut/prompts/`, runs tool-use calls with structured JSON output,
   handles retries with exponential backoff, exposes a `call(prompt_name,
   schema, **vars)` interface and a `call_vision(prompt_name, image, schema)`
   variant. Model: `claude-sonnet-4-6`.
3. **`transcribe.py`** ← **STARTING POINT for implementation pass.**
   ffmpeg audio extract + mlx-whisper + cache. End-to-end with a tiny
   fixture so the user can verify before we move on.
4. **`takes.py`** — silence split, fuzzy script align, Claude best-take pick.
5. **`broll.py`** — ffmpeg frame extract, Pillow contact sheet, Claude vision.
6. **`match.py`** — sentence chunking + Claude match call.
7. **`fcpxml.py`** — XML construction, Premiere/Resolve smoke test.
8. **`cli.py`** — final wiring of all modules behind the Typer CLI.

## Caching

Everything cacheable is keyed by `sha256(mtime + size + absolute_path)` of
the source file. Cache layout:

```
.roughcut-cache/
  transcripts/<hash>.json     # Transcript
  broll/<hash>.json           # Clip
  broll-grids/<hash>.png      # contact sheet (for debugging)
  claude/<call_hash>.json     # raw tool-use responses, optional
```

Cache writes are atomic (tmp file + rename).

## Conventions

- All prompts in `roughcut/prompts/*.md`, loaded at runtime — never inlined.
- Every Claude call uses tool-use for structured output. Never freeform
  parsing.
- Pydantic models are the single source of truth for shapes.
- Modules stay under 200 LOC; refactor before exceeding.
- No GUI, no web server, no database. JSON on disk.

## Out of scope (v1)

Music, color/audio adjustments, multicam, speaker diarization, frontend,
auth, billing, cloud.
