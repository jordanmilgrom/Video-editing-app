"""Tool descriptions, written for the agent (when to use, not just what)."""

# ---- shared / sync --------------------------------------------------------

LIST_CLIPS = (
    "Inventory the video files in a folder via ffprobe and return their "
    "codec, duration, frame rate, resolution, and size. Fast and "
    "synchronous — no job_id involved.\n\n"
    "Call this FIRST when starting a rough cut. `folder` MUST be "
    "absolute. The summary returns `clip_paths` (just the filenames) "
    "and `clips_path` (a JSON file with full ClipMeta)."
)

GET_PROJECT_PATHS = (
    "Return the interview folder, b-roll folder, and script path the "
    "user configured at install time, plus the active `cache_dir`.\n\n"
    "Call this FIRST in any new chat session — combined with "
    "`list_jobs`, it tells you whether prior work is already on disk."
)

GET_SYSTEM_STATUS = (
    "Preflight diagnostics: bundled python loadable, ffmpeg/ffprobe "
    "executable, libmlx.dylib resolvable (i.e. `import mlx_whisper` "
    "works), whisper model cached, cache dir writable, free disk space.\n\n"
    "Call this when the user reports something broken before diving "
    "into specific tools. The `problems` field of the summary lists "
    "the keys that failed; `details` has the full picture."
)

GENERATE_FCPXML = (
    "Assemble an FCPXML v1.10 timeline from a SequenceSpec and write "
    "it to disk. A-roll on V1, b-roll inserts on V2. Synchronous.\n\n"
    "Call this LAST in doc/interview mode. `output_path` MUST be "
    "absolute and end with `.fcpxml`."
)

EXTRACT_FRAME_GRID = (
    "Sample N frames from a video, tile them into a JPEG contact sheet, "
    "and return the image inline so you can vision-read it.\n\n"
    "JPEG quality 75 keeps the inlined image well under Claude Desktop's "
    "1 MB tool-result cap. Synchronous (~1s per clip)."
)

GET_CLIP_THUMBNAIL = (
    "Extract a single frame from a video at a specific timecode and "
    "return it as MCP image content. Synchronous."
)

# ---- async-job tools ------------------------------------------------------

_ASYNC_NOTE = (
    "\n\n**This tool runs asynchronously.** It returns immediately "
    "with `{job_id, status: 'started'}`. Poll `check_job_status(job_id)` "
    "until `status` is `succeeded`, then read `result_summary` (and "
    "`result_path` for the persisted full output).\n\n"
    "Idempotent: re-calling with identical inputs returns the existing "
    "succeeded job's result instantly — no work."
)

TRANSCRIBE_VIDEO = (
    "Transcribe one video file locally with mlx-whisper. Returns a "
    "job_id; on completion, `result_summary.transcript_path` is what "
    "the downstream tools (`cluster_takes_by_silence`, "
    "`align_takes_to_script`, `diarize_speakers`, "
    "`pick_angle_per_segment`) consume.\n\n"
    "**Model defaults to `'small'`** (~480 MB, bundled in the .dxt — "
    "zero-download first run, plenty accurate for clear-speaker "
    "podcast / interview / scripted-VO content). Other choices: "
    "`'medium'`, `'large-v3'` (best quality, ~3 GB, English-strong but "
    "polyglot), `'large-v3-turbo'` (large-v3 quality at ~half the "
    "size and ~2x speed). Power users can pass an `org/repo` HF id "
    "directly.\n\n"
    "If the requested model isn't on disk, the job auto-downloads it "
    "as step 1 — `check_job_status` reports `current_step` so you can "
    "tell whether you're waiting on download or on transcription. To "
    "pre-fetch a bigger model without blocking transcription, call "
    "`prewarm_model('large-v3')` separately.\n\n"
    "Call on interview / podcast clips, not b-roll. Use "
    "`language=\"auto\"` unless you know the code."
) + _ASYNC_NOTE

CLUSTER_TAKES_BY_SILENCE = (
    "Group transcript segments into clusters separated by silence. "
    "Use this when there is NO script. Pass `transcript_path` from a "
    "succeeded `transcribe_video` job."
) + _ASYNC_NOTE

ALIGN_TAKES_TO_SCRIPT = (
    "Fuzzy-match each transcript segment against the lines of a "
    "script. Pass `transcript_path` and the full `script_text` "
    "(read the script file into memory first)."
) + _ASYNC_NOTE

MC_DETECT_GROUPS = (
    "Find clips in a folder that were rolling simultaneously by "
    "cross-correlating their audio waveforms. Each detected group is "
    "written to its own file under cache/multicam-groups/; pick the "
    "group_path you want and pass it to `diarize_speakers` and "
    "`pick_angle_per_segment`."
) + _ASYNC_NOTE

MC_DIARIZE_SPEAKERS = (
    "Per transcript segment, decide which speaker is talking by "
    "comparing mic RMS across the synced clips. Works because podcasts "
    "use one lav per host. No pyannote, no ML."
) + _ASYNC_NOTE

MC_PICK_ANGLE_PER_SEGMENT = (
    "Decide which camera to use for each segment. Primary angle = "
    "current speaker's own camera. Reaction shots fire every "
    "`reaction_interval_sec` for `reaction_hold_sec`."
) + _ASYNC_NOTE

GENERATE_MULTICAM_FCPXML = (
    "Emit an FCPXML v1.10 timeline from a list of AngleSelections. "
    "Each angle becomes a straight cut on V1. Synchronous.\n\n"
    "Pass `angles_path` from a succeeded `pick_angle_per_segment` job."
)

# ---- job management -------------------------------------------------------

CHECK_JOB_STATUS = (
    "Look up the status of an async job by its `job_id`.\n\n"
    "Returns: status, progress_pct, current_step, started_at, "
    "eta_seconds, result_path / result_summary (when succeeded), "
    "error / traceback / hint (when failed).\n\n"
    "Poll every few seconds until status is one of: succeeded, failed, "
    "cancelled, interrupted. If a worker process died mid-job this "
    "tool detects it and flips status to `interrupted` on read."
)

LIST_JOBS = (
    "List recent jobs in this cache dir. Use to recover context after "
    "starting a fresh chat: any earlier transcription / clustering / "
    "diarization that completed shows up here with its result_path.\n\n"
    "Optional `status` filter: one of started/running/succeeded/"
    "failed/cancelled/interrupted. `limit` caps the result count "
    "(default 20)."
)

CANCEL_JOB = (
    "Stop a running job. Sends SIGTERM, escalates to SIGKILL after a "
    "short grace period. Idempotent — calling on a job already in a "
    "terminal state is a no-op."
)

RESUME_JOB = (
    "Restart a failed / interrupted / cancelled job. Per-step caches "
    "(extracted WAV, downloaded whisper model) survive across runs, "
    "so resuming is much cheaper than the first attempt.\n\n"
    "If the job already succeeded, returns the cached result and does "
    "no work. If still running, returns an error — `cancel_job` first."
)

READ_TRANSCRIPT = (
    "Page through a saved transcript JSON. Returns segments with "
    "start/end timecodes, capped at `max_chars` so the result stays "
    "under Desktop's 1 MB cap. Use this in documentary mode to "
    "actually READ a clip after `transcribe_video` finishes — the "
    "transcribe summary only gives you 200 chars.\n\n"
    "`transcript_path` is what `transcribe_video` returns. "
    "`start_segment` defaults to 0; on a long clip, follow up with "
    "`start_segment=next_start` from the previous response to read "
    "the rest. `has_more` tells you when you're done. Synchronous."
)

SEARCH_TRANSCRIPTS = (
    "Case-insensitive substring search across every cached transcript "
    "in the active cache dir. Returns top hits with a few segments of "
    "context on either side. Lets you find moments matching a topic "
    "across an entire shoot without reading anything in full.\n\n"
    "Optional `folder_path` scopes the search to transcripts whose "
    "source video lives under that folder. `context_segments` "
    "controls the window (default 2). Synchronous."
)

SUMMARIZE_CLIP = (
    "Deterministic snippet view of one clip's transcript: opening "
    "200 chars, closing 200 chars, longest continuous segment, total "
    "speech length, segment count, duration. NO LLM call — just facts "
    "about the transcript shape.\n\n"
    "Use this to scan many clips fast after a batch `transcribe_video` "
    "pass, then `read_transcript` only the ones that look interesting. "
    "Synchronous."
)

PREWARM_MODEL = (
    "Pre-fetch a whisper model in the background so a later transcribe "
    "is instant. Use this when you know the user will need `large-v3` "
    "but you don't want to block their first transcription on a "
    "multi-minute download.\n\n"
    "Choices: `'small'` (~480 MB, bundled — already cached), "
    "`'medium'` (~1.5 GB), `'large-v3'` (~3 GB, best quality), "
    "`'large-v3-turbo'` (~1.6 GB, large-v3 quality at 2x speed). "
    "Idempotent: spawning a prewarm for an already-cached model "
    "returns instantly with `already_cached: true`."
) + _ASYNC_NOTE
