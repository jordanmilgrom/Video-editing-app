# Example workflow: build a rough cut

Once roughcut is wired into your agent ([setup guide](./agent-setup.md)),
this is the prompt you paste into a fresh chat to kick off a rough cut.
The agent does the reasoning; roughcut provides the deterministic
capabilities (transcribe, cluster, frame grid, FCPXML).

Two variants below — pick the one that matches whether you have a
script.

---

## Variant 1: no script

Replace the three paths with your real paths, then paste into Claude
Desktop:

> *I want a rough cut. Use the **roughcut** MCP tools end-to-end.*
>
> *Interview footage: `/Users/me/footage/interview`*
> *B-roll footage: `/Users/me/footage/broll`*
> *Output: `/Users/me/cut.fcpxml`*
> *Frame rate: 23.976, 1920×1080.*
>
> *Plan and execute these steps. Show me the FCPXML summary at the end.*
>
> 1. *Call `list_clips` on both folders so we know what's there. Report
>    counts and any clips that look unusable.*
> 2. *For each interview file, call `transcribe_video`. Don't transcribe
>    b-roll.*
> 3. *For each transcript, call `cluster_takes_by_silence` (default
>    threshold). Each cluster represents one continuous spoken phrase.
>    When a phrase is read multiple times in a row it shows up as
>    multiple clusters with similar text.*
> 4. *Identify cluster groups that share the same intent (repeat reads
>    of the same line). For each group, pick the cleanest read — no
>    filler words, no false starts, complete sentence, prefer later
>    takes when otherwise tied. Note the chosen clip path and in/out
>    timecodes.*
> 5. *For each b-roll clip, call `extract_frame_grid`. Look at the
>    image yourself. Note: subject (1 noun phrase), motion (camera and
>    subject), mood (one adjective), 3–8 short tags, and the strongest
>    2–5s window from the timecodes on the sheet.*
> 6. *Walk the chosen-take text sentence by sentence. For each sentence,
>    decide if a b-roll insert would help. If yes, pick 0–3 inserts from
>    your library by semantic match. Avoid reusing the same clip
>    back-to-back when alternatives exist.*
> 7. *Assemble the `SequenceSpec` JSON:*
>    - *`aroll`: list of `{source_path, in_sec, out_sec}` in playback
>      order — the chosen takes concatenated.*
>    - *`broll`: list of `{source_path, clip_in_sec, clip_out_sec,
>      aroll_offset_sec}` — `aroll_offset_sec` is the time on the
>      assembled A-roll timeline (not the source's timecode).*
> 8. *Call `generate_fcpxml` with that spec and the output path.
>    Report the summary.*
>
> *If any tool returns `ok=false`, stop, show me the error, and ask
> for guidance before continuing.*

---

## Variant 2: with a script

When you know what the subject was supposed to say, line up takes
against the script instead of guessing from silence boundaries.

Paste this and replace the four paths:

> *Build a rough cut using the **roughcut** MCP tools.*
>
> *Interview footage: `/Users/me/footage/interview`*
> *B-roll footage: `/Users/me/footage/broll`*
> *Script: `/Users/me/script.txt` (read this file first)*
> *Output: `/Users/me/cut.fcpxml`*
>
> *Steps:*
>
> 1. *`list_clips` on both folders.*
> 2. *`transcribe_video` on each interview file.*
> 3. *Read the script file. For each transcript, call*
>    *`align_takes_to_script` passing the script text. You'll get,*
>    *per line, candidate segments sorted by match confidence.*
> 4. *For each script line, pick the best read from its candidates.
>    Confidence 1.0 is an exact match; below 0.55 the tool didn't
>    return it at all. Prefer the most confident candidate; if there
>    are ties (e.g. multiple takes of the same line), pick the
>    cleanest read.*
> 5. *Make the A-roll list from your picks in script order.*
> 6. *B-roll: `extract_frame_grid` each clip, build a library, match
>    inserts sentence-by-sentence as in the no-script flow.*
> 7. *`generate_fcpxml`, report summary.*

---

## What to expect

A 10-minute interview + 30 b-roll clips, no script:

- `list_clips`: instant.
- `transcribe_video`: 1–3 minutes per interview file on first run; zero
  on reruns (cached).
- `extract_frame_grid`: ~1 second per b-roll clip; cached after first run.
- Agent reasoning + tool plumbing: a few minutes.
- `generate_fcpxml`: instant.

Total wall-clock: roughly **5 minutes** end-to-end on an M-series Mac,
mostly transcription. Reruns are seconds.

Open the produced FCPXML in Premiere Pro (`File → Import`) or DaVinci
Resolve (`File → Import Timeline → File`). You'll see A-roll on V1 and
b-roll on V2. Source clips relink by absolute path.

---

## Tips

- **Iterate fast on b-roll choices** by asking the agent: *"redo step
  6 only — try fresher b-roll picks that avoid repeating the same
  clip."* Steps 1–5 are cached; only the match step re-runs.
- **Tighten the cut** by asking: *"trim 200ms of silence from the head
  of every A-roll segment before generating FCPXML."* The agent shifts
  the in-points; nothing else changes.
- **Spot-check a moment** with `get_clip_thumbnail` if a contact-sheet
  cell looks promising — pass the clip path and the cell's timecode.
- **Don't transcribe b-roll.** It's a waste of time on silent or
  music-only footage and clogs the cache.
- **Always use absolute paths.** Drag a folder from Finder into the
  Terminal to copy its absolute path quickly.
