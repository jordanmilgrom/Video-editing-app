# Example workflows

Once roughcut is wired into your agent ([setup guide](./agent-setup.md)),
these are the prompts you paste into a fresh chat to kick off a rough
cut. The agent does the reasoning; roughcut provides the deterministic
capabilities (transcribe, cluster, frame grid, FCPXML).

**v0.5.0 size-bound returns.** Tools that can grow large
(`transcribe_video`, `cluster_takes_by_silence`, `align_takes_to_script`,
`list_clips`, `detect_multicam_groups`, `diarize_speakers`,
`pick_angle_per_segment`) write their full payload to a JSON file under
`~/Video-editing-app/cache/` and return a small summary with a
`*_path` field. Downstream tools take those paths as input — the agent
never receives raw transcripts or alignments inline.

Three variants: no-script doc, scripted doc, and multicam podcast.

---

## Variant 1: doc / interview, no script

> *I want a rough cut. Use the **roughcut** MCP tools end-to-end.*
>
> *Interview footage: `/Users/me/footage/interview`*
> *B-roll footage: `/Users/me/footage/broll`*
> *Output: `/Users/me/cut.fcpxml`*
> *Frame rate: 23.976, 1920×1080.*
>
> 1. *Call `list_clips` on both folders. The summary's `clip_paths`
>    field is the list you'll iterate over.*
> 2. *For each interview file, call `transcribe_video`. Each call's
>    `summary.transcript_path` is what the next tool consumes — keep
>    track of one path per file.*
> 3. *For each `transcript_path`, call `cluster_takes_by_silence`
>    (default threshold). Read the clusters back from
>    `summary.clusters_path` if you need details; the path is a JSON
>    file under the cache dir.*
> 4. *Identify clusters that are repeat reads of the same intent. Pick
>    the cleanest take per group — prefer no filler words, no false
>    starts, complete sentence, later takes when otherwise tied.*
> 5. *For each b-roll clip, call `extract_frame_grid`. The contact
>    sheet is inlined as an image — vision-read it directly. Note:
>    subject, motion, mood, 3–8 tags, strongest 2–5s window.*
> 6. *Walk the chosen-take text sentence by sentence. Decide where
>    b-roll helps; pick 0–3 inserts each.*
> 7. *Assemble the `SequenceSpec` JSON and call `generate_fcpxml`.*
>
> *Stop on the first `ok=false`.*

---

## Variant 2: doc / interview, with a script

> *Build a rough cut using the **roughcut** MCP tools.*
>
> *Interview footage: `/Users/me/footage/interview`*
> *Script: `/Users/me/script.txt` (read this file first)*
> *Output: `/Users/me/cut.fcpxml`*
>
> 1. *`list_clips` on the interview folder.*
> 2. *`transcribe_video` on each clip → keep each `transcript_path`.*
> 3. *Read the script file. For each `transcript_path` call*
>    *`align_takes_to_script` with the script text. The summary tells
>    you `lines_with_match` vs `lines_without_match`. Read the full
>    alignments from `alignments_path` if you want to see candidates.*
> 4. *For each script line, pick the best candidate. Confidence ≥ 0.55;
>    1.0 is an exact match. Prefer cleanest read among same-confidence.*
> 5. *Make the A-roll list from your picks in script order.*
> 6. *(Same b-roll loop as Variant 1.)*
> 7. *`generate_fcpxml`.*

---

## Variant 3: multicam podcast (NEW in v0.5.0)

For a 2-host (or 3+) podcast where each host has their own camera + lav
mic, recorded in parallel (not jam-synced).

> *I'm cutting a podcast. Two hosts (Alice and Bob), each with their
> own camera and lav.*
>
> *Folder: `/Users/me/podcast/ep42` — contains both camera files.*
> *Output: `/Users/me/ep42.fcpxml`*
>
> 1. *Call `detect_multicam_groups` on the folder. You'll get one
>    group per episode; for a single episode you'll see exactly one
>    group. The summary lists `clip_paths` and the per-clip
>    `offsets_sec` (so you know which camera started first).*
> 2. *Pick the largest group's `group_path` from the summary. If there
>    are multiple groups, ask me which to use.*
> 3. *Transcribe ONE clip from the group with `transcribe_video` (the
>    longer one if the durations differ). Keep the
>    `transcript_path`.*
> 4. *Call `diarize_speakers` with the `transcript_path`, the
>    `group_path`, and `speaker_labels=["Alice", "Bob"]` (assuming
>    Alice's camera file is first in the group; flip if not).*
> 5. *Call `pick_angle_per_segment` with the transcript, the
>    diarization, and the group. Use defaults (`reaction_interval_sec=30`,
>    `reaction_hold_sec=2`) unless I asked for something else.*
> 6. *Call `generate_multicam_fcpxml` with the `angles_path` and the
>    output path.*
>
> *Don't `align_takes_to_script` — that's doc mode.*
> *Don't `cluster_takes_by_silence` either — multicam keeps every word.*

---

## What to expect

A 10-minute interview + 30 b-roll clips, no script: roughly **5
minutes** end-to-end on an M-series Mac, mostly transcription. Reruns
are seconds (cache hits).

A 60-minute 2-camera podcast: roughly **8–12 minutes** end-to-end. Most
of the time is one full transcription pass; sync + diarize + angle
picking are seconds.

Open the produced FCPXML in Premiere Pro (`File → Import`) or DaVinci
Resolve (`File → Import Timeline → File`).

---

## Tips

- **Iterate without re-transcribing** by reusing the `transcript_path`.
  Caches survive reruns.
- **Spot-check a moment** with `get_clip_thumbnail` (clip path + timecode).
- **Don't transcribe b-roll.** Waste of time on silent or music-only
  footage.
- **Always use absolute paths.** Drag a folder from Finder into chat to
  copy its absolute path.
- **Multicam offsets are seconds**, not frames. If `detect_multicam_groups`
  finds 0.0 offsets for both clips, the cameras were genuinely
  jam-synced — or the audio is too quiet to correlate.
