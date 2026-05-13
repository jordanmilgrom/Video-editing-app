# roughcut

AI rough-cut tool for video editors. Point it at a folder of interview
footage and a folder of b-roll clips; it emits an FCPXML rough cut that
imports cleanly into Premiere Pro and DaVinci Resolve.

- A-roll on V1 (best take per cluster)
- B-roll on V2 at semantically matched moments
- Local transcription via mlx-whisper (Apple Silicon)
- All structured outputs come from Claude via JSON tool-use

> v0.1.0 — early development. See `CLAUDE.md` for the architecture and
> module-by-module build order.

## Requirements

- macOS on Apple Silicon (mlx-whisper)
- Python 3.11+
- `ffmpeg` and `ffprobe` on `PATH`
- `ANTHROPIC_API_KEY` in the environment

## Install

```bash
pip install -e ".[dev]"
```

## Usage

```bash
python -m roughcut \
  --interview ./footage/interview \
  --broll     ./footage/broll \
  --output    ./cut.fcpxml \
  [--script   ./script.txt]
```

The first run transcribes everything and analyzes every b-roll clip; reruns
hit `.roughcut-cache/` and are cheap.

## Project layout

```
roughcut/
  cli.py          Typer entrypoint
  models.py       Pydantic v2 contracts
  transcribe.py   ffmpeg + mlx-whisper, cached
  takes.py        cluster takes, pick best read
  broll.py        contact-sheet frames → Claude vision
  match.py        sentence → b-roll matcher
  fcpxml.py       FCPXML v1.10 emission
  claude.py       anthropic SDK wrapper
  prompts/        all prompts as markdown (loaded at runtime)
tests/            pytest with tiny fixtures
```

## Develop

```bash
pytest
```

Tests stub out the actual mlx-whisper call so they run anywhere ffmpeg is
available.
