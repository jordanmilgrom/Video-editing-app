# roughcut

**A first-draft video editor that runs on your Mac.** Point it at a folder of
interview footage and a folder of b-roll clips, and it spits out a project
file you can open in Premiere Pro (or DaVinci Resolve). Your best takes are
on the main video track. Visually relevant b-roll is dropped on the track
above, at the moments where it fits.

It is not finishing your edit. It is doing the first pass — the slow,
tedious part — so you can start cutting from something instead of from
nothing.

---

## What you need (one-time setup)

You need a Mac with an Apple Silicon chip (M1, M2, M3, M4). The
transcription engine doesn't work on Intel Macs.

You need three things installed. If you don't have them, the steps below
walk through it.

### 1. Homebrew

Homebrew is a tool for installing other tools. To install it, open the
**Terminal** app (search for "Terminal" in Spotlight) and paste this in:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the prompts. It will ask for your password.

### 2. ffmpeg and Python

In Terminal, run:

```
brew install ffmpeg python@3.11
```

### 3. An Anthropic API key

This tool uses Claude (the AI). You need an API key, which is like a
password that lets the tool talk to Claude on your behalf.

- Go to https://console.anthropic.com/
- Sign up or sign in.
- Click "API Keys" in the left menu.
- Click "Create Key", copy the long string that starts with `sk-ant-...`.

You'll paste it into Terminal once, and it'll remember.

---

## Installing this tool

In Terminal, navigate to wherever you saved this project folder. For
example, if it's on your Desktop:

```
cd ~/Desktop/Video-editing-app
```

Then run:

```
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

That's it. The first command makes a private Python environment for this
project. The second activates it. The third installs everything.

To save your API key so the tool can use it:

```
echo 'export ANTHROPIC_API_KEY="sk-ant-paste-your-key-here"' >> ~/.zshrc
source ~/.zshrc
```

(Replace `sk-ant-paste-your-key-here` with your actual key.)

---

## Running it

Make two folders somewhere on your Mac:

- **Interview folder**: your interview footage (e.g. `.mp4`, `.mov`, `.mxf`).
- **B-roll folder**: all your b-roll clips.

Then in Terminal (with the `.venv` activated — you'll see `(.venv)` in
your prompt):

```
python -m roughcut \
  --interview /path/to/interview/folder \
  --broll     /path/to/broll/folder \
  --output    /path/to/save/cut.fcpxml
```

Drag the folders from Finder into Terminal to paste their paths.

If you have a script (the words the subject is supposed to say), add:

```
  --script /path/to/script.txt
```

The tool will print progress as it goes:

```
[1/5] Transcribing interview footage ...
[2/5] Picking best takes ...
[3/5] Analyzing b-roll ...
[4/5] Matching b-roll to A-roll ...
[5/5] Writing FCPXML ...
```

The first run on a 10-minute interview with 30 b-roll clips should finish
in about 3-5 minutes on an M-series Mac. Running it again on the same
footage takes seconds — it remembers what it already did.

---

## Opening the result

In Premiere Pro: **File → Import → choose `cut.fcpxml`**. A new sequence
appears with your interview on V1 (the main video track) and b-roll on V2
(stacked above). Your source media is referenced by absolute path, so the
clips should appear without re-linking.

In DaVinci Resolve: **File → Import Timeline → File → choose `cut.fcpxml`**.

---

## What the flags mean

| Flag | What it does |
| ---- | ------------ |
| `--interview` | Folder containing your interview video files. |
| `--broll` | Folder containing your b-roll clips. |
| `--output` | Where to save the result (give it a `.fcpxml` ending). |
| `--script` | Optional. Path to a `.txt` of what the subject is supposed to say. Makes take detection more accurate. |
| `--model` | Optional. Different transcription model. Default is the most accurate. Power users only. |
| `--fps` | Optional. Output sequence frame rate. Default 23.976. |
| `--cache-dir` | Optional. Where the tool saves its working files. Default is a hidden `.roughcut-cache` folder inside your project. |

---

## Troubleshooting

**"Command not found: python"** — You need to activate the virtual
environment. From the project folder, run `source .venv/bin/activate`.
You'll see `(.venv)` appear in your prompt when it works.

**"ANTHROPIC_API_KEY not set"** — Run the `echo 'export ...'` step from
the installing section, with your real key, then **close and reopen
Terminal**.

**"ffmpeg not found on PATH"** — Run `brew install ffmpeg`.

**Premiere says "media is offline"** — The FCPXML uses absolute paths.
If you moved the source files after generating the FCPXML, run it again
to update the paths.

**It's slow** — The first run on each interview file is slow because
transcription is real work. Reruns hit the cache. If you want to iterate
on the b-roll matching without re-transcribing, just rerun the same
command — only the matching step will repeat.

---

## What's happening under the hood

(You don't need to read this. It's here so the next person who works on
the code can find their way around.)

```
roughcut/
  cli.py          The command-line entrypoint. Just orchestrates.
  transcribe.py   ffmpeg pulls the audio. mlx-whisper transcribes it.
  takes.py        Splits the transcript into reads of the same line.
                  Claude picks the cleanest read.
  broll.py        Pulls 16 frames from each clip, tiles them into a
                  contact sheet, shows it to Claude vision, gets back
                  subject/motion/mood/tags.
  match.py        Walks the chosen interview sentence by sentence and
                  asks Claude which b-roll clip(s) would visually
                  support each line.
  fcpxml.py       Writes the result as an FCPXML file Premiere/Resolve
                  can open.
  models.py       Type definitions everything else uses.
  claude.py       The Anthropic API client wrapper.
  prompts/        All the instructions sent to Claude live here as
                  Markdown files — easy to read and edit without
                  touching Python.
```

The pipeline is `transcribe → takes → b-roll → match → fcpxml`. Each
stage caches its outputs to `.roughcut-cache/`, so re-running is cheap.

---

## Limits of v1

- Doesn't do music, color, audio mixing, or any other "finishing" work.
- Doesn't handle multicam.
- Doesn't separate speakers — assumes one subject.
- RAW formats (`.braw`, `.r3d`, `.ari`) aren't supported yet. Transcode
  to ProRes or H.264 first.
- It's an opinionated first draft. Expect to recut everything. That's
  the point — start from a draft, not a blank timeline.
