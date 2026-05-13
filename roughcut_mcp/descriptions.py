"""Tool descriptions, written for the agent (when to use, not just what).

Pulled out of `tools.py` so each module stays under 200 LOC.
"""

LIST_CLIPS = (
    "Inventory the video files in a folder via ffprobe and return their "
    "codec, duration, frame rate, resolution, and size.\n\n"
    "Call this FIRST when starting a rough cut, before transcribing or "
    "frame-grabbing — it's cheap, read-only, and tells you what footage "
    "exists. Use it on the interview folder and the b-roll folder "
    "separately so you can plan the cut.\n\n"
    "`folder` MUST be an absolute path. Relative paths are rejected with "
    "`error=\"relative_path\"`. Recursion is on by default."
)

TRANSCRIBE_VIDEO = (
    "Transcribe one video file locally with mlx-whisper and return a "
    "Transcript with word-level timestamps. Cached by file hash + model, "
    "so reruns are free.\n\n"
    "Call this on interview clips (not b-roll). Run once per interview "
    "file. Use `language=\"auto\"` unless you know the language code "
    "(\"en\", \"es\", ...). Use the default model unless the user asks "
    "for a faster variant.\n\n"
    "`video_path` MUST be absolute. Cache lives at `cache_dir` if "
    "provided, else $ROUGHCUT_CACHE_DIR, else ~/.cache/roughcut."
)

CLUSTER_TAKES_BY_SILENCE = (
    "Group transcript segments into take clusters by silence boundaries. "
    "Deterministic; no judgment about which take is best — you decide "
    "that by reading the cluster contents.\n\n"
    "Call this on each transcript when there is NO script. A cluster of "
    "one segment is fine (the line was nailed on the first try); a "
    "cluster of many is a sequence of repeats you'll need to triage.\n\n"
    "Pass the Transcript exactly as returned by transcribe_video (under "
    "the `transcript` field of its ToolResponse). `silence_threshold_sec` "
    "defaults to 2.0."
)

ALIGN_TAKES_TO_SCRIPT = (
    "Fuzzy-match each transcript segment against the lines of a script. "
    "Returns, per script line, the candidate segments sorted by "
    "confidence. You pick which candidate(s) to use.\n\n"
    "Call this on each transcript when the user provided a script. Skip "
    "when no script. Confidence is difflib SequenceMatcher ratio (0..1); "
    "only candidates above 0.55 are returned."
)

GENERATE_FCPXML = (
    "Assemble an FCPXML v1.10 timeline from a SequenceSpec and write it "
    "to disk. A-roll segments land on V1 in list order; b-roll inserts "
    "land on V2 at their `aroll_offset_sec` (measured from the start of "
    "the assembled A-roll, not from any individual segment).\n\n"
    "Call this LAST. By the time you call it you should already have "
    "picked your chosen takes and matched b-roll. `output_path` MUST be "
    "absolute and end with `.fcpxml`.\n\n"
    "Returns `summary` with `aroll_count`, `broll_count`, `duration_sec` "
    "so you can confirm what was written."
)

EXTRACT_FRAME_GRID = (
    "Sample N frames evenly across a video clip, tile them into a 4x4 "
    "contact sheet with timecode overlays on each cell, and return the "
    "image so you can vision-read it.\n\n"
    "Call this on each b-roll clip to understand what's in it. You read "
    "the sheet visually and decide subject / motion / mood / tags / best "
    "in-out window — those decisions are NOT made by this tool. Cached "
    "by file hash + params; reruns are free.\n\n"
    "`video_path` MUST be absolute. Set `overlay_timecodes=False` for a "
    "clean sheet (useful only for human review)."
)

GET_CLIP_THUMBNAIL = (
    "Extract a single frame from a video at a specific timecode and "
    "return it as MCP image content.\n\n"
    "Use this when you want a closer look at one moment of a clip — "
    "after a contact-sheet pass surfaces an interesting cell. "
    "`video_path` MUST be absolute."
)

GET_PROJECT_PATHS = (
    "Return the interview folder, b-roll folder, and script path the "
    "user configured when installing the roughcut extension. Values "
    "come from the Claude Desktop user_config UI; each field may be "
    "unset (returned as null).\n\n"
    "Call this FIRST in any new conversation, before asking the user "
    "for paths. If a field is set, use it directly. If a field is "
    "null, ask the user to drag the folder/file into chat. Never make "
    "up paths."
)
