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

That's it. Open a new Claude Desktop chat, look for the tools / plug
icon, and `roughcut` should be listed with eight tools.

ffmpeg, ffprobe, mlx-whisper, **a portable Python 3.11 interpreter**,
and all Python deps are bundled inside the `.dxt` — no Terminal, no
Homebrew, no `pip install`. Claude Desktop launches the bundled
interpreter directly, so it doesn't matter what version of `python3`
your Mac has (or doesn't have) on its system PATH.

### Requirements

- macOS with Apple Silicon (M1 / M2 / M3 / M4). Intel Macs cannot run
  local Whisper transcription.
- Claude Desktop with a paid subscription (Pro / Team / Enterprise).

---

## 60-second "try it"

Once installed, ask Claude in a new chat:

> *"Use `get_project_paths` to check what I configured, then run
> `list_clips` on the interview folder."*

You should get back codec, duration, frame rate, resolution, and size
for each video file in that folder. If you skipped the user_config
prompts at install time, drag the folder from Finder into chat to get
its path.

---

## Build a full rough cut

Open [`docs/example-workflow.md`](docs/example-workflow.md). Copy the
prompt into a fresh Claude Desktop chat and let the agent run.

A 10-minute interview plus 30 b-roll clips takes roughly **5 minutes
end-to-end** on an M-series Mac, mostly transcription. Reruns hit the
cache and are essentially free.

Open the resulting `.fcpxml` in Premiere Pro
(`File → Import → choose the file`) or DaVinci Resolve
(`File → Import Timeline → File`). Interview audio sits on V1, b-roll on
V2. Clips relink to source by absolute path.

---

## The eighteen tools

| Tool                         | Sync / Async | Mode      | What it does                                                                  |
| ---------------------------- | ------------ | --------- | ----------------------------------------------------------------------------- |
| `get_project_paths`          | sync         | meta      | Return the interview / b-roll / script paths set at install time + cache dir. |
| `get_system_status`          | sync         | meta      | Preflight + per-model cache inventory (which models are bundled vs to-fetch). |
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

### Resilience model (v0.6.0)

Real transcriptions exceed Claude Desktop's tool-call timeout. The six
**async** tools spawn a detached worker subprocess and return
immediately with a `job_id`. The agent polls `check_job_status(job_id)`
until status is `succeeded`, then reads `result_summary` (and
`result_path` for the persisted full output).

Jobs survive:
- Tool-call timeouts (the work runs out-of-process).
- Claude Desktop quit/restart (worker stays detached; on next launch,
  the server scans the jobs dir and marks dead jobs `interrupted` so
  the agent can `resume_job` them).
- Fresh chat sessions (`list_jobs` shows recent activity; the agent
  picks up any succeeded job's `result_path` instead of redoing work).

Identical inputs hit the same `job_id` (sha256 over tool name + path +
size + mtime + model), so re-running transcribe on the same file is a
free cache hit — no work.

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
(`check_job_status` shows `current_step` so you can tell whether you're
waiting on the download or the transcription itself). Call
`prewarm_model('large-v3')` ahead of time to pre-fetch in the
background without blocking transcription.

The agent decides which to call and when. The
[`docs/example-workflow.md`](docs/example-workflow.md) prompt
orchestrates them end-to-end.

---

## What's NOT in v0.6

Music, color, audio mixing, RAW formats (`.braw` / `.r3d` / `.ari` —
transcode to ProRes/H.264 first), pyannote-style diarization for
single-mic podcasts (we rely on per-host lavs and mic-dominance — fine
for typical setups, wrong for podcasts mixed to a single track),
checkpoint-level resume inside an in-progress transcription (`resume_job`
restarts the whole transcription; the extracted audio + downloaded
model survive across runs, so it's still much faster than the first
attempt). It's an opinionated first draft. Expect to recut everything
— that's the point.

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

Then wire `.venv/bin/roughcut-mcp` into Claude Desktop manually via
`~/Library/Application Support/Claude/claude_desktop_config.json`
(see [`docs/agent-setup.md`](docs/agent-setup.md) for the JSON
snippet).

To rebuild `roughcut.dxt` from source, see [`BUILD.md`](BUILD.md).

The architecture is documented in [`CLAUDE.md`](CLAUDE.md). The MCP
boundary contract is in [`REFACTOR.md`](REFACTOR.md).
