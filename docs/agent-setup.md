# Adding roughcut to your AI agent

roughcut is an MCP (Model Context Protocol) server. To use it you point a
compatible agent — Claude Desktop, Claude Code, etc. — at the
`roughcut-mcp` command that `pip install` put in your virtual
environment. The agent then sees seven tools and can call them on your
behalf.

This page walks through wiring it up and what to do when something
doesn't work.

---

## Before you start

Make sure you've already done the install steps in the project
[`README.md`](../README.md):

```
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Confirm the command is on disk:

```
which roughcut-mcp
```

You should see a path like `.../Video-editing-app/.venv/bin/roughcut-mcp`.
Copy that path — you'll need it in a moment.

---

## Claude Desktop (macOS)

The config file lives at:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

Open it (or create it) and paste:

```json
{
  "mcpServers": {
    "roughcut": {
      "command": "/ABSOLUTE/PATH/TO/Video-editing-app/.venv/bin/roughcut-mcp"
    }
  }
}
```

Replace `/ABSOLUTE/PATH/TO/Video-editing-app` with the path you got
from `which roughcut-mcp` (drop the trailing `.venv/bin/roughcut-mcp`
in the path itself — keep it in the config above).

If you already have other MCP servers configured, just add the
`"roughcut": { ... }` entry inside the existing `"mcpServers"` object.

**Quit Claude Desktop completely (Cmd-Q) and reopen it.** The config is
only re-read on launch.

Open a new chat and look for the tools icon (plug / hammer). You should
see `roughcut` listed with seven tools.

## Claude Desktop (Windows / Linux)

Same config shape, different file location:

- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

Use the absolute path to `roughcut-mcp.exe` (Windows) or
`roughcut-mcp` (Linux).

## Claude Code

Claude Code is the terminal-based agent. Two options:

**Option A — add via CLI:**

```
claude mcp add roughcut /ABSOLUTE/PATH/TO/Video-editing-app/.venv/bin/roughcut-mcp
```

**Option B — edit the config file directly:**

The config lives at `~/.claude.json`. The shape is the same as Claude
Desktop. Add a `roughcut` entry under `mcpServers`.

Restart Claude Code with `/exit` and `claude` to pick up the change.

---

## Verifying it works

In a chat, ask the agent:

> *"What roughcut tools do you have available?"*

The agent should list seven: `list_clips`, `transcribe_video`,
`cluster_takes_by_silence`, `align_takes_to_script`, `generate_fcpxml`,
`extract_frame_grid`, `get_clip_thumbnail`.

Then ask:

> *"Use list_clips on /Users/me/some-folder-with-videos."*

(Replace the path with a real absolute folder on your Mac that contains
`.mp4` / `.mov` / `.mxf` files.) You should get codec / duration / fps
/ resolution / file size for each clip.

---

## Troubleshooting

### "I don't see roughcut in the tools panel"

- The command path in the config must be absolute. `~` doesn't expand.
- Quit the agent completely and reopen — config is only re-read on
  launch. On macOS, Cmd-Q (not just close window).
- Make sure the command is executable: `ls -la /path/to/roughcut-mcp`
  should show `-rwxr-xr-x`. If not, your venv install didn't finish;
  rerun `pip install -e .`.
- Check the agent's log directory (next section) for startup errors.

### "Tool errors with `ffprobe_unavailable` or `ffmpeg_unavailable`"

ffmpeg/ffprobe isn't on the `PATH` the agent uses to launch the
server. On macOS this almost always means you need to install ffmpeg:

```
brew install ffmpeg
```

If you installed ffmpeg via a non-Homebrew route, make sure it's on
the `PATH` of the shell the agent is launched from.

### "Transcription is slow on the first run"

That's expected. mlx-whisper transcribes a 10-minute clip in roughly
1–2 minutes on an M-series Mac. Reruns hit the cache at
`~/.cache/roughcut/transcripts/...` and are essentially free.

You can move the cache by setting an environment variable before
launching the agent:

```
export ROUGHCUT_CACHE_DIR=/Volumes/scratch/roughcut-cache
```

### "Where are the logs?"

The roughcut server logs to **stderr**. Stdout is reserved for the MCP
protocol and is silent.

- **Claude Desktop on macOS**: `~/Library/Logs/Claude/mcp-server-roughcut.log`
- **Claude Code**: `claude mcp logs roughcut`

If you don't see a log file the server may never have launched —
double-check the absolute path in the config.

### "I get `not_a_directory` or `not_a_file` errors"

All paths sent to roughcut tools must be **absolute**. The tool
intentionally rejects relative paths so the agent has to ask the user
for an unambiguous location. If your chat said *"use the folder Desktop"*,
re-ask with the full path: `/Users/you/Desktop`.

### "Premiere says 'media is offline' after I open the FCPXML"

The FCPXML references source files by absolute path. If you moved or
renamed source files between generating the FCPXML and opening it,
Premiere can't find them. Either move them back, or regenerate the
FCPXML with the new paths.

### "An MCP error response says `internal_error`"

That's a catch-all for unexpected exceptions. The actual exception
type and message are in the response under `message`, and the full
traceback is in the log file. Open an issue with that traceback and
the inputs that triggered it.
