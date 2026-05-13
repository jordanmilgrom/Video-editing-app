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

## The twelve tools

| Tool                         | Mode      | What it does                                                                  |
| ---------------------------- | --------- | ----------------------------------------------------------------------------- |
| `get_project_paths`          | meta      | Return the interview / b-roll / script paths set at install time + cache dir. |
| `list_clips`                 | shared    | Inventory a folder of video files (ffprobe).                                  |
| `transcribe_video`           | shared    | Local mlx-whisper transcription with word timestamps.                         |
| `cluster_takes_by_silence`   | doc       | Group transcript segments by silence boundaries.                              |
| `align_takes_to_script`      | doc       | Fuzzy-match transcript segments against script lines.                         |
| `extract_frame_grid`         | doc       | Sample 16 frames from a clip, tile into a JPEG contact sheet.                 |
| `get_clip_thumbnail`         | doc       | One frame at a specific timecode.                                             |
| `generate_fcpxml`            | doc       | Write FCPXML v1.10 from a `SequenceSpec` the agent built.                     |
| `detect_multicam_groups`     | multicam  | Group simultaneously-recorded clips by audio waveform sync.                   |
| `diarize_speakers`           | multicam  | Per-segment speaker labels via mic-RMS dominance (no pyannote).               |
| `pick_angle_per_segment`     | multicam  | Pick camera per segment + sprinkle reaction shots.                            |
| `generate_multicam_fcpxml`   | multicam  | Write FCPXML laying out angles as flat cuts on V1.                            |

**Size-bounded returns (v0.5.0):** every tool whose payload could exceed
Claude Desktop's 1 MB tool-result cap writes its full output to a JSON
file under `~/Video-editing-app/cache/` and returns a small summary
with a `*_path` field. The agent passes those paths to downstream tools.

The agent decides which to call and when. The
[`docs/example-workflow.md`](docs/example-workflow.md) prompt
orchestrates them end-to-end.

---

## What's NOT in v0.5

Music, color, audio mixing, RAW formats (`.braw` / `.r3d` / `.ari` —
transcode to ProRes/H.264 first), pyannote-style diarization for
single-mic podcasts (we rely on per-host lavs and mic-dominance — fine
for typical setups, wrong for podcasts mixed to a single track). It's
an opinionated first draft. Expect to recut everything — that's the
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

Then wire `.venv/bin/roughcut-mcp` into Claude Desktop manually via
`~/Library/Application Support/Claude/claude_desktop_config.json`
(see [`docs/agent-setup.md`](docs/agent-setup.md) for the JSON
snippet).

To rebuild `roughcut.dxt` from source, see [`BUILD.md`](BUILD.md).

The architecture is documented in [`CLAUDE.md`](CLAUDE.md). The MCP
boundary contract is in [`REFACTOR.md`](REFACTOR.md).
