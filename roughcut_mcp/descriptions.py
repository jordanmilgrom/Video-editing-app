"""Tool descriptions, written for the agent (when to use, not just what).

Every description follows the v0.6.5 contract:

  1. One-sentence summary at the top.
  2. WHEN to call this tool vs. its neighbors (the discriminating fact).
  3. Critical params with short defaults / value ranges.
  4. Async tools end with `_ASYNC_NOTE` describing the poll pattern.

Response shape every tool returns: `data` (rich result dict),
`next_steps` (suggested follow-up calls), and on failure `error`/`message`.
The legacy field `summary` is still populated as a mirror of `data`.
"""

# ---- shared / sync --------------------------------------------------------

LIST_CLIPS = (
    "Inventory the video files in a folder via ffprobe and return their "
    "codec, duration, frame rate, resolution, and size. Fast and "
    "synchronous — no job_id involved.\n\n"
    "Call this FIRST when starting a rough cut. `folder` MUST be "
    "absolute. The result's `data` returns `clip_paths` (just the "
    "filenames) and `clips_path` (a JSON file with full ClipMeta)."
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
    "works), whisper model cached, cache dir writable, free disk space, "
    "worker pool size + live workers + queue depth.\n\n"
    "Call this when the user reports something broken before diving "
    "into specific tools. The `problems` field of `data` lists "
    "the keys that failed; `details` has the full picture."
)

GET_SERVER_LOGS = (
    "Tail the trailing N lines from `cache/watchdog.log` and "
    "`cache/jobs/worker-*.log`. Default 100 lines, max 1000.\n\n"
    "Use when a job has failed silently or the server feels stuck — "
    "the worker logs usually have a Python traceback. Synchronous, "
    "filesystem-read only; never blocks on workers."
)

GENERATE_FCPXML = (
    "Write four timeline files + EDL relink sidecar from one call:\n"
    "  - `<basename>.fcpxml` (Final Cut Pro 1.10)\n"
    "  - `<basename>.xml`    (Premiere — FCP 7 XMEML v5)\n"
    "  - `<basename>.edl`    (CMX 3600, universal V1 fallback)\n"
    "  - `<basename>.otio`   (OpenTimelineIO — Resolve import + otiotool)\n"
    "  - `<basename>.relink.csv` (reel → source mapping for the EDL)\n\n"
    "All four share the same basename derived from `output_path`. A-roll "
    "is laid out contiguously on V1, b-roll inserts nest on V2. The "
    "FCPXML is self-validated before the tool returns success — invalid "
    "output is reported as `ok=False` with details, never silently shipped.\n\n"
    "**SequenceSpec schema (v0.6.5 canonical names):**\n"
    "```\n"
    "{\n"
    "  \"name\": \"my-cut\",\n"
    "  \"fps\": 23.976, \"width\": 1920, \"height\": 1080,\n"
    "  \"aroll\": [\n"
    "    {\"source_path\": \"/abs/clip.mov\",\n"
    "     \"source_in_sec\": 0.0, \"source_out_sec\": 5.0}\n"
    "  ],\n"
    "  \"broll\": [\n"
    "    {\"source_path\": \"/abs/broll.mov\",\n"
    "     \"source_in_sec\": 0.0, \"source_out_sec\": 2.0,\n"
    "     \"timeline_offset_sec\": 1.5}\n"
    "  ]\n"
    "}\n"
    "```\n"
    "Call this LAST. The result's `data.import_hints` tells you which "
    "file to point each NLE at; `edl_note` explains EDL reel relinking."
)

VALIDATE_FCPXML = (
    "Re-validate an FCPXML file: well-formedness, ref/id resolution, "
    "asset/<media-rep> presence, and (if xmllint is on PATH) external "
    "DTD validation. Returns `ok=True` only if every check passed."
)

EXTRACT_FRAME_GRID = (
    "Sample N frames from a video, tile them into a JPEG contact sheet, "
    "and return the image inline so you can vision-read it."
)

GET_CLIP_THUMBNAIL = (
    "Extract a single frame from a video at a specific timecode and "
    "return it as MCP image content. Synchronous."
)

# ---- async-job tools ------------------------------------------------------

_ASYNC_NOTE = (
    "\n\n**This tool runs asynchronously.** It returns immediately "
    "with `{job_id, status: 'queued'|'started', queue_position}`. Poll "
    "`check_job_status(job_id)` until `status` is `succeeded`, then "
    "read `result_summary` (the rich dict) and `result_path` (the "
    "persisted full output).\n\n"
    "Idempotent: re-calling with identical inputs returns the existing "
    "succeeded job's result instantly — no work."
)

TRANSCRIBE_FOLDER = (
    "One-call batch transcription: enumerates every video in `folder` "
    "and spawns one transcribe_video job per clip. Skips clips already "
    "cached and clips whose ffprobe reports no audio stream.\n\n"
    "Use this INSTEAD of calling transcribe_video N times. Synchronous "
    "but fast — just spawns worker subprocesses. The transcription "
    "itself runs async via the pool."
)

TRANSCRIBE_VIDEO = (
    "Transcribe one video file locally with mlx-whisper. Returns a "
    "job_id; on completion, `result_summary.transcript_path` is the "
    "cached transcript.\n\n"
    "**Model param table:**\n"
    "  - `'small'`   — ~480 MB (BUNDLED). Production. DEFAULT.\n"
    "  - `'medium'`  — ~1.5 GB, better on noisy audio.\n"
    "  - `'large-v3'` — ~3 GB, best accuracy.\n"
    "  - `'large-v3-turbo'` — ~1.6 GB, large-v3 quality at 2× speed.\n\n"
    "Call on interview / podcast clips, not b-roll."
) + _ASYNC_NOTE

CLUSTER_TAKES_BY_SILENCE = (
    "Group transcript segments into clusters separated by silence. "
    "Use this when there is NO script."
) + _ASYNC_NOTE

ALIGN_TAKES_TO_SCRIPT = (
    "Fuzzy-match each transcript segment against the lines of a script."
) + _ASYNC_NOTE

MC_DETECT_GROUPS = (
    "Find clips in a folder that were rolling simultaneously by "
    "cross-correlating their audio waveforms."
) + _ASYNC_NOTE

MC_DIARIZE_SPEAKERS = (
    "Per transcript segment, decide which speaker is talking by "
    "comparing mic RMS across the synced clips."
) + _ASYNC_NOTE

MC_PICK_ANGLE_PER_SEGMENT = (
    "Decide which camera to use for each segment."
) + _ASYNC_NOTE

GENERATE_MULTICAM_FCPXML = (
    "Emit an FCPXML v1.10 timeline from a list of AngleSelections."
)

# ---- job management -------------------------------------------------------

CHECK_JOB_STATUS = "Look up the status of an async job by its `job_id`."

LIST_JOBS = (
    "List recent jobs in this cache dir. Use to recover context after "
    "starting a fresh chat.\n\n"
    "**Pagination:** `limit` defaults to 100, capped at 1000."
)


RESTART_WORKERS = "Self-heal: kill every live worker subprocess, respawn the pool."

CANCEL_JOB = "Stop a running job. SIGTERM then SIGKILL. Idempotent."

RESUME_JOB = (
    "Restart a failed / interrupted / cancelled job. Per-step caches "
    "survive across runs."
)

READ_TRANSCRIPT = (
    "Page through a saved transcript JSON. Returns segments with "
    "start/end timecodes, capped at `max_chars` (default 900_000)."
)

SEARCH_TRANSCRIPTS = (
    "Case-insensitive substring search across every cached transcript "
    "in the active cache dir."
)

SUMMARIZE_CLIP = (
    "**No LLM call. Deterministic.** Snippet view of one clip's transcript."
)

FIND_SILENCES = (
    "Return silence ranges and tight speech sub-segments inside a "
    "specific time window of a transcript."
)

ANALYZE_SCENE = (
    "**Level 2 of the video understanding stack.** Assembles a rich "
    "multi-modal bundle for one clip: `num_frames` (default 25) frames "
    "spanning the whole video as a contact sheet with timecodes, motion "
    "spans + cut boundaries, audio dBFS envelope stats, per-shot transcript "
    "windows (if a transcript is cached), plus a JSON schema TEMPLATE the "
    "agent fills in.\n\n"
    "**Workflow (three-step):**\n"
    "  1. `analyze_scene(video_path)` — server returns the bundle.\n"
    "  2. Agent vision-reads the contact sheet, correlates frames with "
    "the cut boundaries, motion labels, audio dB, and transcript. Fills "
    "in the schema — shot-by-shot subject, camera work, composition, "
    "color mood, quality issues (bumps, focus, exposure), notable "
    "events, plus clip-wide `is_blooper` / `is_retake` flags and a "
    "one-line summary.\n"
    "  3. `save_scene_analysis(video_path, analysis)` — persists the "
    "completed analysis. Cached forever under "
    "`cache/scene-analyses/<sha>.json`.\n\n"
    "Once cached, `search_scenes('kitchen')` and `read_scene_analysis` "
    "let future sessions find and reuse the analysis without vision-"
    "reading again. `index_project` inlines it too.\n\n"
    "This is what makes editorial judgment possible — 'is clip A long "
    "enough to fill the gap?' 'which retake was cleanest?' 'does this "
    "cut look good?' all consume the structured analysis instead of the "
    "raw video.\n\n"
    "Synchronous, ~3-5s per minute of source."
)

SAVE_SCENE_ANALYSIS = (
    "Persist a completed SceneAnalysis JSON keyed on the video's content "
    "hash. Call after vision-reading the analyze_scene bundle and filling "
    "in the schema.\n\n"
    "**Schema:**\n"
    "```\n"
    "{\n"
    "  \"one_line\": \"wide shot of factory floor, workers moving\",\n"
    "  \"shots\": [\n"
    "    {\n"
    "      \"shot_index\": 0, \"start_sec\": 0.0, \"end_sec\": 4.5,\n"
    "      \"type\": \"wide\", \"subject\": \"factory\",\n"
    "      \"camera\": \"static\", \"composition\": \"rule-of-thirds\",\n"
    "      \"color_mood\": \"warm industrial\", \"quality\": \"clean\",\n"
    "      \"notable_events\": [\"worker enters frame at 3.2s\"]\n"
    "    }\n"
    "  ],\n"
    "  \"usability_verdict\": \"good cutaway, avoid 8-9s (bump)\",\n"
    "  \"quality_issues\": [\"camera bump 8-9s\", \"soft focus 10-11s\"],\n"
    "  \"color_palette\": \"warm industrial\",\n"
    "  \"is_blooper\": false, \"is_retake\": false,\n"
    "  \"tags\": [\"factory\", \"cutaway\"]\n"
    "}\n"
    "```\n"
    "`video_path`, `video_hash`, `duration_sec`, and `shot_count` are "
    "auto-filled by the server if omitted.\n\n"
    "Idempotent: subsequent saves overwrite (agents refine as they see "
    "more frames)."
)

READ_SCENE_ANALYSIS = (
    "Return the cached SceneAnalysis for a video, or "
    "`ok=False, error='not_a_file'` if none exists yet."
)

SEARCH_SCENES = (
    "Case-insensitive substring search across every cached SceneAnalysis. "
    "Matches against one_line, usability_verdict, color_palette, tags, and "
    "every shot's subject / composition / color_mood / quality / "
    "notable_events.\n\n"
    "Use for high-level content search once analyses are cached: "
    "'find clips with worker' returns matching clips; 'find shots with "
    "camera bump' surfaces bad takes; 'find blooper' finds flagged clips."
)

DETECT_SCENES = (
    "Shot / scene boundary detection with a representative frame per shot. "
    "Composes analyze_motion (finds cuts) with frame extraction (one "
    "thumbnail per shot)."
)

ANALYZE_MOTION = (
    "Frame-diff a video clip (sampled at `sample_hz` Hz, default 2) "
    "and return spans tagged static / slow_pan / fast_pan / cut. "
    "Always call this BEFORE picking b-roll source_in/out_sec ranges."
)

DETECT_FILLERS = (
    "Find filler words ('um', 'uh', 'you know', 'like') inside a "
    "transcript window with word-level timestamps."
)

DESCRIBE_CLIP = (
    "Persist a structured caption for a b-roll clip so future sessions "
    "can find it by content without re-vision-reading."
)

SEARCH_BROLL = (
    "Case-insensitive substring search over cached b-roll captions."
)

RENDER_PREVIEW = (
    "Render a low-res MP4 preview of a SequenceSpec so you can "
    "sanity-check the cut BEFORE opening Final Cut. 480×270 H.264."
)

ADD_HANDLES_TO_SPEC = (
    "Return a copy of a SequenceSpec with `handle_sec` of source-handle "
    "pad on every A-roll and B-roll segment."
)

RENDER_CUT = (
    "Render a SequenceSpec at delivery quality (H.264/AAC MP4) via ffmpeg "
    "concat. Distinct from `render_preview`.\n\n"
    "**Presets:** `720p30`, `1080p30` (DEFAULT), `1080p60`, `4k30`.\n\n"
    "Runs asynchronously through the worker pool."
) + _ASYNC_NOTE

FIND_AUDIO_SILENCES = (
    "Detect silences by examining the actual audio waveform (RMS dBFS)."
)

DETECT_BREATHS = (
    "Find breaths / lip smacks / inhales between transcribed words."
)

DETECT_FALSE_STARTS = (
    "Find interview false starts: 'I- I-I think' / 'the the'."
)

TIGHTEN_TAKE = (
    "**The fused editorial-tightening pass.** Run every applicable "
    "detector with ONE audio decode and return ONE merged list of "
    "speech-only sub-segments."
)

WATCH_SEGMENT = (
    "**The 'actually watch the clip' tool.** Returns a contact-sheet image "
    "of `num_frames` (default 16) evenly-spaced frames from "
    "`[start_sec, end_sec]` with timecodes overlaid, PLUS the transcript "
    "words inside that window PLUS audio energy stats — all bundled "
    "into one tool result.\n\n"
    "Use when you're picking BETWEEN candidate takes / segments."
)

INDEX_PROJECT = (
    "One-call project inventory. Enumerates every video in `folder` and "
    "returns a compact entry per clip that composes: ffprobe metadata, "
    "transcript status, cached b-roll caption, and cached scene analysis "
    "(v0.12).\n\n"
    "Use this AT THE START of any documentary session."
)

LOOKUP_TRANSCRIPT_BY_VIDEO_PATH = (
    "Given an absolute video path, find the cached transcript JSON "
    "for that file (or report `found=False`)."
)

PREWARM_MODEL = (
    "Pre-fetch a whisper model in the background so a later transcribe "
    "is instant."
) + _ASYNC_NOTE
