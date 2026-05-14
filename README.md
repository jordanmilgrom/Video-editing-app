# roughcut

**A first-draft video editor that runs from your AI agent.**

You point your chat agent (Claude Desktop) at a folder of interview
footage and a folder of b-roll clips. The agent picks the cleanest
takes, decides where b-roll fits, and writes a Premiere / DaVinci
Resolve project file. You open the file in your editor and take it
from there.

It is not finishing your edit. It is doing the first pass — the slow,
tedious part — so you can start cutting from something instead of from
nothing.

This project ships as a **Claude Desktop Extension (`.dxt`)**. The
reasoning happens in your agent (using your Claude Desktop
subscription, not your API key); roughcut does the deterministic work
locally: transcribe, cluster takes, contact-sheet frames, build FCPXML.

---

## Install (recommended)

1. Download the latest `roughcut.dxt` from the
   [Releases page](https://github.com/jordanmilgrom/video-editing-app/releases).
2. **Double-click `roughcut.dxt`.** Claude Desktop opens, asks you to
   confirm the install, then prompts for three optional paths:
   - **Interview folder** — where your sit-down clips live
   - **B-roll folder** — where your cutaways / supporting visuals live
   - **Script (`.txt` or `.md`)** — leave blank if you have no script
3. Click **Install**.

ffmpeg, ffprobe, mlx-whisper, **a portable Python 3.11 interpreter**,
**the whisper-small model** (~480 MB), and all Python deps are bundled
inside the `.dxt`. First-run transcription works fully offline.

### Requirements

- macOS with Apple Silicon (M1 / M2 / M3 / M4). Intel Macs cannot run
  local Whisper transcription.
- Claude Desktop with a paid subscription (Pro / Team / Enterprise).

---

## The twenty-one tools

| Tool                         | Sync / Async | Mode      | What it does                                                                  |
| ---------------------------- | ------------ | --------- | ----------------------------------------------------------------------------- |
| `get_project_paths`          | sync         | meta      | Return the interview / b-roll / script paths set at install time + cache dir. |
| `get_system_status`          | sync         | meta      | Preflight + per-model cache inventory + worker pool config + queue depth.     |
| `list_clips`                 | sync         | shared    | Inventory a folder of video files (ffprobe).                                  |
| `transcribe_video`           | **async**    | shared    | mlx-whisper transcription; default model `small` is bundled (offline ok).     |
| `prewarm_model`              | **async**    | shared    | Pre-fetch a bigger whisper model (`medium`, `large-v3`, ...) in background.   |
| `cluster_takes_by_silence`   | **async**    | doc       | Group transcript segments by silence (job).                                   |
| `align_takes_to_script`      | **async**    | doc       | Fuzzy-match segments to script lines (job).                                   |
| `extract_frame_grid`         | sync         | doc       | 16 frames → JPEG contact sheet, returned inline.                              |
| `get_clip_thumbnail`         | sync         | doc       | One frame at a specific timecode.                                             |
| `generate_fcpxml`            | sync         | doc       | Write FCPXML v1.10 from a `SequenceSpec`.                                     |
| `detect_multicam_groups`     | **async**    | multicam  | Audio-sync clips into groups (job).                                           |
| `diarize_speakers`           | **async**    | multicam  | Per-segment speaker via mic-RMS dominance (job).                              |
| `pick_angle_per_segment`     | **async**    | multicam  | Pick camera per segment + reaction shots (job).                               |
| `generate_multicam_fcpxml`   | sync         | multicam  | Flat-cut FCPXML from an AngleSelection list.                                  |
| `check_job_status`           | sync         | jobs      | Poll a job by `job_id`.                                                       |
| `list_jobs`                  | sync         | jobs      | Recent jobs — recover context after a chat restart.                           |
| `cancel_job`                 | sync         | jobs      | SIGTERM → SIGKILL a running job.                                              |
| `resume_job`                 | sync         | jobs      | Re-run a failed / interrupted / cancelled job.                                |
| `read_transcript`            | sync         | docs      | Page through a saved transcript JSON in chunks (~900 KB cap per call).        |
| `search_transcripts`         | sync         | docs      | Case-insensitive substring search across cached transcripts.                  |
| `summarize_clip`             | sync         | docs      | Deterministic snippet view of one clip (no LLM call).                         |

### Documentary workflow (v0.6.3)

Documentary editing is "find the story in what was actually said,"
which doesn't fit the scripted alignment flow. The three sync
**docs** tools above let chat-side Claude scan a 32-clip shoot
without you having to paste anything in.

A typical session looks like:

> *Drop the interview folder into chat. Then ask:*
>
> > *"Transcribe everything in that folder. When all jobs are done,*
> > *call `summarize_clip` on each one and give me the headline of*
> > *each interview."*
>
> *Claude fires 32 async `transcribe_video` jobs (the v0.6.3 worker*
> *pool runs them serially without lockup), polls `list_jobs` until*
> *all are succeeded, then calls `summarize_clip` per result_path.*
> *You get a one-screen briefing.*
>
> > *"Search for moments about [topic]."*
>
> *`search_transcripts` returns hits across every clip with context.*
>
> > *"Read clip 7 in full."*
>
> *`read_transcript` pages the full transcript in 900 KB chunks.*
>
> > *"Build me a rough cut focused on the [theme] moments."*
>
> *Claude assembles a `SequenceSpec` from the hit timestamps and*
> *calls `generate_fcpxml`. You open the result in Premiere.*

### Concurrency model (v0.6.3)

The async tools enqueue against a **single persistent worker
subprocess** per cache dir. Pool size is configurable via
`ROUGHCUT_WORKER_POOL_SIZE`; default `1`. mlx-whisper on Apple Silicon
doesn't parallelize across whisper instances usefully, so the default
keeps the GPU saturated by exactly one job at a time. The MCP server
itself never blocks on subprocess startup — it writes the job record,
appends to the queue, and returns. `check_job_status` / `list_jobs` /
`cancel_job` only touch on-disk JSON, never IPC with the worker.

`get_system_status.workers` shows the configured limit, live worker
pids, and the current queue depth.

### Resilience model (v0.6.0)

Real transcriptions exceed Claude Desktop's tool-call timeout. The six
**async** tools spawn a detached worker subprocess and return
immediately with a `job_id`. The agent polls `check_job_status(job_id)`
until status is `succeeded`, then reads `result_summary` (and
`result_path` for the persisted full output).

Jobs survive Claude Desktop quit/restart; `list_jobs` lets a fresh
chat session pick up where the old one left off. Identical inputs hit
the same `job_id` (sha256 over tool name + path + size + mtime +
model), so re-running transcribe on the same file is a free cache hit
— no work.

**Size-bounded returns:** every async tool's result is a JSON file on
disk under `~/Video-editing-app/cache/`. Tool results to the agent are
always small (a path + a few counts).

### Whisper models (v0.6.1)

`transcribe_video`'s `model` parameter accepts short names:

| Short name        | Repo                                         | Size      | When to use                              |
| ----------------- | -------------------------------------------- | --------- | ---------------------------------------- |
| `small` (default) | `mlx-community/whisper-small-mlx`            | ~480 MB   | **Bundled in the .dxt.** Clear-speaker podcast, interview, scripted VO. Fast. |
| `medium`          | `mlx-community/whisper-medium-mlx`           | ~1.5 GB   | Noisy audio, accented speakers.          |
| `large-v3`        | `mlx-community/whisper-large-v3-mlx`         | ~3 GB     | Best quality, multilingual.              |
| `large-v3-turbo`  | `mlx-community/whisper-large-v3-turbo`       | ~1.6 GB   | Large-v3 quality at ~2x speed.           |

Non-bundled models are auto-downloaded in-flight on first use
(`check_job_status` shows `current_step`). Call
`prewarm_model('large-v3')` ahead of time to pre-fetch in the
background without blocking transcription.

The agent decides which to call and when. The
[`docs/example-workflow.md`](docs/example-workflow.md) prompt
orchestrates them end-to-end.

---

## What's NOT in v0.6

Music, color, audio mixing, RAW formats (`.braw` / `.r3d` / `.ari` —
transcode to ProRes/H.264 first), pyannote-style diarization for
single-mic podcasts (we rely on per-host lavs and mic-dominance),
checkpoint-level resume inside an in-progress transcription. It's an
opinionated first draft. Expect to recut everything — that's the
point.

---

## For developers

If you want to hack on the Python source rather than install the
shipped `.dxt`:

```
git clone https://github.com/jordanmilgrom/video-editing-app.git
cd video-editing-app
brew install ffmpeg python@3.11        # one-time
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

To rebuild `roughcut.dxt` from source, see [`BUILD.md`](BUILD.md).

The architecture is documented in [`CLAUDE.md`](CLAUDE.md). The MCP
boundary contract is in [`REFACTOR.md`](REFACTOR.md).
