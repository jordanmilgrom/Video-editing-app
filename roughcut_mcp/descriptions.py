"""Tool descriptions, written for the agent (when to use, not just what)."""

# ---- shared --------------------------------------------------------------

LIST_CLIPS = (
    "Inventory the video files in a folder via ffprobe and return their "
    "codec, duration, frame rate, resolution, and size.\n\n"
    "Call this FIRST when starting a rough cut. Cheap, read-only. Use it "
    "on the interview folder and the b-roll folder separately so you can "
    "plan the cut.\n\n"
    "`folder` MUST be an absolute path. Returns a small summary with "
    "`clips_path` (JSON file of full ClipMeta records) and `clip_paths` "
    "(just the file paths, for quick reference) — the full inventory "
    "lives on disk so tool results stay under Desktop's 1 MB cap."
)

TRANSCRIBE_VIDEO = (
    "Transcribe one video file locally with mlx-whisper and return a "
    "summary referencing the cached Transcript.\n\n"
    "Call this on interview clips (not b-roll). Run once per file. Use "
    "`language=\"auto\"` unless you know the language code.\n\n"
    "Returns `summary.transcript_path` — pass it to the doc-mode tools "
    "(`cluster_takes_by_silence`, `align_takes_to_script`) or to the "
    "multicam tools (`diarize_speakers`, `pick_angle_per_segment`)."
)

# ---- doc / interview mode -------------------------------------------------

CLUSTER_TAKES_BY_SILENCE = (
    "Group transcript segments into take clusters by silence boundaries.\n\n"
    "Call this when there is NO script. Pass `transcript_path` (from "
    "transcribe_video's summary). The full cluster list is written to "
    "disk; the summary returns `clusters_path` plus counts."
)

ALIGN_TAKES_TO_SCRIPT = (
    "Fuzzy-match each transcript segment against the lines of a script. "
    "Returns, per script line, candidate segments sorted by confidence. "
    "You pick which candidate(s) to use.\n\n"
    "Pass `transcript_path` (from transcribe_video). On long scripts the "
    "full alignment list can exceed 1 MB; you'll receive "
    "`alignments_path` in the summary and read it from disk as needed."
)

GENERATE_FCPXML = (
    "Assemble an FCPXML v1.10 timeline from a SequenceSpec and write it "
    "to disk. A-roll on V1, b-roll inserts on V2.\n\n"
    "Call this LAST in doc/interview mode. `output_path` MUST be "
    "absolute and end with `.fcpxml`."
)

EXTRACT_FRAME_GRID = (
    "Sample N frames evenly across a video clip, tile them into a 4x4 "
    "contact sheet with timecode overlays, and return the image inline "
    "so you can vision-read it.\n\n"
    "Output is JPEG quality 75 so it stays well under Desktop's 1 MB "
    "tool-result cap. `video_path` MUST be absolute."
)

GET_CLIP_THUMBNAIL = (
    "Extract a single frame from a video at a specific timecode and "
    "return it as MCP image content."
)

# ---- multicam / podcast mode ---------------------------------------------

MC_DETECT_GROUPS = (
    "Find clips in a folder that were rolling simultaneously by "
    "cross-correlating their audio waveforms. No jam sync required.\n\n"
    "Use for parallel-camera podcasts (each host has their own camera "
    "+ lav mic). Returns groups, each written to its own file under the "
    "cache dir. Pick the group you want (e.g. one group per episode) "
    "and pass its `group_path` to `diarize_speakers` and "
    "`pick_angle_per_segment`.\n\n"
    "Skip when there's only one camera. `folder` MUST be absolute."
)

MC_DIARIZE_SPEAKERS = (
    "Per transcript segment, decide which speaker is talking by "
    "comparing mic RMS across the synced clips. The host whose lav is "
    "loudest is the speaker.\n\n"
    "Works because podcasts use per-host lavs; mic bleed is much quieter "
    "than the speaker's own voice. No ML, no pyannote, no model weights. "
    "Pass `transcript_path` from one clip in the group and the "
    "`group_path` from `detect_multicam_groups`. Optional "
    "`speaker_labels` lets you name speakers; default is 'Speaker 1', "
    "'Speaker 2', etc."
)

MC_PICK_ANGLE_PER_SEGMENT = (
    "Decide which camera to use for each segment.\n\n"
    "Primary angle = the current speaker's own camera. Every "
    "`reaction_interval_sec` (default 30s) the picker briefly cuts to "
    "another speaker's camera for `reaction_hold_sec` (default 2s) so "
    "the rough cut isn't a static loop.\n\n"
    "Inputs are paths from earlier tools. Output is `angles_path` "
    "pointing at the full AngleSelection list; you read it from disk if "
    "you want to tweak before generating the FCPXML."
)

MC_GENERATE_MULTICAM_FCPXML = (
    "Emit an FCPXML v1.10 timeline from a list of AngleSelections, with "
    "each angle laid out as a straight cut on V1. The result opens "
    "cleanly in Premiere / DaVinci Resolve and lets the editor refine "
    "the cuts.\n\n"
    "Pass `angles_path` from `pick_angle_per_segment`. `output_path` "
    "MUST be absolute and end with `.fcpxml`."
)

# ---- meta ----------------------------------------------------------------

GET_PROJECT_PATHS = (
    "Return the interview folder, b-roll folder, and script path the "
    "user configured at install time, plus the active `cache_dir`.\n\n"
    "Call this FIRST in any new conversation, before asking the user "
    "for paths. If a field is null the user did not configure it — ask "
    "them to drag the folder/file into chat."
)
