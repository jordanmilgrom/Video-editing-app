# roughcut

**A first-draft video editor that runs from your AI agent.**

You point your chat agent (Claude Desktop, Claude Code) at a folder of
interview footage and a folder of b-roll clips. The agent picks the
cleanest takes, decides where b-roll fits, and writes a Premiere /
DaVinci Resolve project file. You open the file in your editor and
take it from there.

It is not finishing your edit. It is doing the first pass — the slow,
tedious part — so you can start cutting from something instead of from
nothing.

This project ships as an **MCP server** with seven tools. The reasoning
happens in your agent (using its subscription, not your API key); we
just expose deterministic video capabilities: transcribe, cluster takes,
contact-sheet frames, build FCPXML.

---

## What you need (one-time setup)

A Mac with Apple Silicon (M1 / M2 / M3 / M4). Local transcription does
not work on Intel.

Three pieces of software, installed in this order:

### 1. Homebrew

Homebrew installs other tools. Open **Terminal** (search for it in
Spotlight) and paste:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the prompts.

### 2. ffmpeg and Python 3.11

In the same Terminal:

```
brew install ffmpeg python@3.11
```

### 3. Claude Desktop

Download from <https://claude.ai/download>. Sign in.

(You don't need an Anthropic API key. Your Claude Desktop subscription
pays for reasoning. roughcut does the deterministic work locally.)

---

## Install roughcut

In Terminal, navigate to wherever this project lives — for example, if
you saved it on your Desktop:

```
cd ~/Desktop/Video-editing-app
```

Then:

```
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

That's it. You now have a `roughcut-mcp` command inside
`.venv/bin/`. Confirm it:

```
which roughcut-mcp
```

You should see an absolute path.

---

## Wire roughcut into Claude Desktop

Open (or create) the config file at:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

Paste this in. **Replace `/ABSOLUTE/PATH/TO/Video-editing-app`** with
the real path on your Mac:

```json
{
  "mcpServers": {
    "roughcut": {
      "command": "/ABSOLUTE/PATH/TO/Video-editing-app/.venv/bin/roughcut-mcp"
    }
  }
}
```

**Quit Claude Desktop completely (Cmd-Q, not just close the window)
and reopen it.** Open a new chat and click the tools / plug icon —
you should see `roughcut` listed with seven tools.

Step-by-step troubleshooting, Windows/Linux paths, and Claude Code
setup are in [`docs/agent-setup.md`](docs/agent-setup.md).

---

## 60-second "try it"

Once the server is wired up, ask Claude in a new chat:

> *"Use the `list_clips` tool on `/Users/you/some-video-folder` and
> tell me what's there."*

(Drag the folder from Finder into your chat to get the path.)

You should get back codec, duration, frame rate, resolution, and size
for each video file in that folder.

---

## Build a full rough cut

Open [`docs/example-workflow.md`](docs/example-workflow.md). Copy the
prompt into a fresh Claude Desktop chat, replace the three or four
paths with your real folder paths, and let the agent run.

A 10-minute interview plus 30 b-roll clips takes roughly **5 minutes
end-to-end** on an M-series Mac, mostly transcription. Reruns hit the
cache and are essentially free.

Open the resulting `.fcpxml` in Premiere Pro
(`File → Import → choose the file`) or DaVinci Resolve
(`File → Import Timeline → File`). Interview audio sits on V1, b-roll on
V2. Clips relink to source by absolute path.

---

## The seven tools

| Tool                       | What it does                                                 |
| -------------------------- | ------------------------------------------------------------ |
| `list_clips`               | Inventory a folder of video files (ffprobe).                 |
| `transcribe_video`         | Local mlx-whisper transcription with word timestamps.        |
| `cluster_takes_by_silence` | Group transcript segments by silence boundaries.             |
| `align_takes_to_script`    | Fuzzy-match transcript segments against script lines.        |
| `extract_frame_grid`       | Sample 16 frames from a clip, tile into a contact sheet.     |
| `get_clip_thumbnail`       | One frame at a specific timecode.                            |
| `generate_fcpxml`          | Write FCPXML v1.10 from a `SequenceSpec` the agent built.    |

The agent decides which to call and when. The
[`docs/example-workflow.md`](docs/example-workflow.md) prompt
orchestrates them end-to-end.

---

## What's NOT in v0.3

Music, color, audio mixing, multicam, speaker diarization, RAW formats
(`.braw` / `.r3d` / `.ari` — transcode to ProRes/H.264 first). It's an
opinionated first draft. Expect to recut everything — that's the point.

---

## For developers

The architecture is documented in [`CLAUDE.md`](CLAUDE.md). The MCP
boundary contract is in [`REFACTOR.md`](REFACTOR.md). Run tests with
`pip install -e ".[dev]" && pytest`.
