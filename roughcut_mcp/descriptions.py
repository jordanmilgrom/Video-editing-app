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
    "Write three timeline files + EDL relink sidecar from one call:\n"
    "  - `<basename>.fcpxml` (Final Cut Pro 1.10)\n"
    "  - `<basename>.xml`    (Premiere — FCP 7 XMEML v5)\n"
    "  - `<basename>.edl`    (CMX 3600, universal V1 fallback)\n"
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
    "Pre-v0.6.5 names (`in_sec`/`out_sec` on a-roll, "
    "`clip_in_sec`/`clip_out_sec`/`aroll_offset_sec` on b-roll) still "
    "work via deprecation alias — both forms are accepted.\n\n"
    "Reusing one source_path across multiple segments is the common "
    "case for documentary cuts; the emitter dedupes to one `<asset>` "
    "regardless of how many surface forms of the path you pass.\n\n"
    "**Editorial pre-flight (v0.7.0):** before calling this, you "
    "should have already:\n"
    "  - Called `find_silences(start_sec, end_sec)` for each long "
    "A-roll take and split it into multiple ARollSegments around the "
    "interior pauses. Otherwise the cut will be full of dead air.\n"
    "  - Called `analyze_motion(video_path)` for each candidate b-roll "
    "source and chosen `source_in/out_sec` only inside `stable_spans` "
    "(static or slow_pan). Otherwise you'll insert cameraman-searching "
    "panning footage.\n\n"
    "**Bounds validation:** every unique source is ffprobed; the call "
    "returns `ok=False` with a per-segment `bounds_errors` list if any "
    "ARollSegment / BRollInsert specifies `source_out_sec` past the "
    "actual file duration. Don't lie about clip lengths.\n\n"
    "Call this LAST. The result's `data.import_hints` tells you which "
    "file to point each NLE at; `edl_note` explains EDL reel relinking."
)

VALIDATE_FCPXML = (
    "Re-validate an FCPXML file: well-formedness, ref/id resolution, "
    "asset/<media-rep> presence, and (if xmllint is on PATH) external "
    "DTD validation. Returns `ok=True` only if every check passed.\n\n"
    "`generate_fcpxml` runs this internally before declaring success; "
    "agents only need to call this explicitly when investigating an "
    "FCP import error on a file they didn't just write."
)

EXTRACT_FRAME_GRID = (
    "Sample N frames from a video, tile them into a JPEG contact sheet, "
    "and return the image inline so you can vision-read it.\n\n"
    "v0.6.5 defaults: `num_frames=9`, `tile_size=256` (long edge in px), "
    "`jpeg_quality=70`. These keep the inline image at ~600 KB so 9 "
    "tiles stay under Claude Desktop's 1 MB tool-result cap. Bump "
    "`tile_size` to 384/512 or `num_frames` to 16 if you need more "
    "detail; either will eat into the byte budget. Synchronous (~1s per clip).\n\n"
    "**For b-roll triage in v0.7.0+, call `analyze_motion` BEFORE this** — "
    "the contact sheet shows you what's IN the clip but not where it's "
    "framing-stable. analyze_motion returns the precise spans you should "
    "pick from. Use the contact sheet to see CONTENT (subject, framing); "
    "use analyze_motion to see WHERE that content is held still."
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
    "succeeded job's result instantly — no work.\n\n"
    "Concurrency: the worker pool is capped at `ROUGHCUT_WORKER_POOL_SIZE` "
    "(default 4). Jobs beyond that sit in the queue with `queue_position` "
    "indicating how many ahead; workers drain the queue strictly FIFO."
)

TRANSCRIBE_VIDEO = (
    "Transcribe one video file locally with mlx-whisper. Returns a "
    "job_id; on completion, `result_summary.transcript_path` is what "
    "the downstream tools (`cluster_takes_by_silence`, "
    "`align_takes_to_script`, `summarize_clip`, `read_transcript`, "
    "`search_transcripts`) consume.\n\n"
    "**Model param table:**\n"
    "  - `'tiny'`    — ~80 MB, fast, low accuracy. Quick draft.\n"
    "  - `'base'`    — ~150 MB, modest accuracy. Casual transcripts.\n"
    "  - `'small'`   — ~480 MB (BUNDLED in .dxt, zero-download). "
    "Production-grade for clear-speaker interviews / VO. DEFAULT.\n"
    "  - `'medium'`  — ~1.5 GB, better accuracy than small. Noisy audio.\n"
    "  - `'large-v3'` — ~3 GB, best accuracy, multilingual.\n"
    "  - `'large-v3-turbo'` — ~1.6 GB, large-v3 quality at 2× speed.\n"
    "Power users can pass any `org/repo` HF id directly.\n\n"
    "**Language param:** v0.6.5 defaults to `'en'` because Whisper "
    "aggressively hallucinates languages on near-silent / low-speech "
    "clips (real-world example: a near-silent drone clip got tagged "
    "as Welsh). Pass `'auto'` to detect; pass an ISO 639-1 code to "
    "force. When the model returns less than 5 segments and 50 chars on "
    "a clip longer than 10s, the result includes "
    "`low_speech_content: true` and a hint — don't try to source "
    "dialogue from that clip.\n\n"
    "If the requested model isn't on disk, the job auto-downloads it "
    "as step 1 — `check_job_status` reports `current_step` so you can "
    "tell whether you're waiting on download or on transcription. To "
    "pre-fetch a bigger model without blocking transcription, call "
    "`prewarm_model('large-v3')` separately FIRST when you know you'll "
    "be batching across many clips.\n\n"
    "Call on interview / podcast clips, not b-roll."
) + _ASYNC_NOTE

CLUSTER_TAKES_BY_SILENCE = (
    "Group transcript segments into clusters separated by silence. "
    "Use this when there is NO script. Pass `transcript_path` from a "
    "succeeded `transcribe_video` job.\n\n"
    "v0.6.5: result includes `preview` — the top 3 clusters by duration "
    "with their text excerpts inline, so the agent can decide which "
    "cluster looks promising without reading the full JSON."
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
    "diarization that completed shows up here with its `result_path`.\n\n"
    "**Pagination:** `limit` defaults to 100, capped at 1000. `offset` "
    "defaults to 0. The result's `data` returns `total_count`, "
    "`returned_count`, and `next_offset` so you can page deterministically "
    "instead of guessing what got truncated. Optional `status` filter "
    "accepts started/queued/running/succeeded/failed/cancelled/interrupted.\n\n"
    "Read-only filesystem call — never blocks on workers."
)


RESTART_WORKERS = (
    "Self-heal: kill every live worker subprocess, then respawn the "
    "configured pool. Use when the agent or user reports the server is "
    "wedged — jobs sitting in `running` forever, `list_jobs` returning "
    "stale state, etc. Cheaper than asking the user to toggle the "
    "Claude Desktop extension off and back on.\n\n"
    "Any in-flight `running` jobs whose worker we kill get flipped to "
    "`interrupted` first so `check_job_status` reflects reality. The "
    "queue is preserved; the new workers drain it from the front."
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
    "start/end timecodes, capped at `max_chars` (default 900_000) so "
    "the result stays under Desktop's 1 MB cap. Use in documentary "
    "mode to actually READ a clip after `transcribe_video` finishes "
    "— the transcribe summary only gives you 200 chars.\n\n"
    "**Pagination:** `start_segment` defaults to 0. When the response's "
    "`has_more` is true, call again with `start_segment=next_start` "
    "to read the next page. `end_segment` is optional and lets you "
    "scope a fixed window. Synchronous."
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
    "**No LLM call. Returns in milliseconds. Deterministic.** Snippet "
    "view of one clip's transcript: first 200 chars (`opening_200_chars`), "
    "last 200 chars (`closing_200_chars`), the longest continuous "
    "segment (`longest_segment_text` + start/end timecodes), total "
    "speech char count, segment count, duration.\n\n"
    "Designed for fast batch scanning: call `summarize_clip` on each "
    "of N transcripts in parallel, decide which clips look interesting, "
    "then `read_transcript` only those in full. Synchronous — no "
    "job_id, no polling."
)

FIND_SILENCES = (
    "Return silence ranges and tight speech sub-segments inside a "
    "specific time window of a transcript. Use this AFTER picking a "
    "long take, BEFORE adding it to a SequenceSpec — drop the raw "
    "take and instead add multiple ARollSegments built from "
    "`speech_sub_segments`. Otherwise the cut will include 30 seconds "
    "of dead air just because they fell inside the chosen take.\n\n"
    "Pure transcript analysis, no audio probing. `min_silence_sec` "
    "defaults to 0.5s — bump to 1.0s for slow-paced interviews, drop "
    "to 0.3s to chase every micro-pause. Returns `silences` (head / "
    "interior / tail tagged), `speech_sub_segments` (the tight inverse "
    "ranges, each ≥0.3s), `total_silence_sec`, and a "
    "`tightened_duration_sec` so you can see the time saved before "
    "committing. Synchronous, milliseconds."
)

ANALYZE_MOTION = (
    "Frame-diff a video clip (sampled at `sample_hz` Hz, default 2) "
    "and return spans tagged static / slow_pan / fast_pan / cut. "
    "Always call this BEFORE picking b-roll source_in/out_sec ranges "
    "— otherwise you'll silently choose spans where the cameraman "
    "was searching for the subject (panning back and forth) or that "
    "straddle a shot boundary, both of which look like garbage on the "
    "timeline.\n\n"
    "Pipeline: ffmpeg samples at `sample_hz` Hz, downsamples to 64×36 "
    "grayscale, numpy computes mean-abs-pixel-diff between consecutive "
    "frames, thresholds bucket each interval. The response includes "
    "`stable_spans` (static + slow_pan, ≥2s) — those are the spans "
    "you should pick from. Synchronous, ~1–3s per minute of source.\n\n"
    "Bump `sample_hz` to 4 for fast-paced footage where 0.5s temporal "
    "resolution misses cuts."
)

DETECT_FILLERS = (
    "Find filler words ('um', 'uh', 'you know', 'like', 'I mean', "
    "'actually', 'basically', etc.) inside a transcript window. "
    "Returns each occurrence as `(start_sec, end_sec)` with exact "
    "word-level timestamps from Whisper.\n\n"
    "v0.8.0 workflow: pair this with `find_silences`. The two outputs "
    "together give you a complete list of moments to skip when "
    "assembling ARollSegments — silences (gaps in speech) plus fillers "
    "(noise inside speech). Merge them, invert into speech_sub_segments, "
    "and you get a cut that's typically 15-30% shorter than the raw "
    "take without losing any meaningful content.\n\n"
    "`patterns` lets you extend the default English set (e.g. add "
    "'right?' for an interview that overuses it). Pure transcript "
    "analysis — no audio probing. Synchronous, milliseconds."
)

DESCRIBE_CLIP = (
    "Persist a structured caption for a b-roll clip so future sessions "
    "can find it by content without re-vision-reading every contact "
    "sheet. The cache lives at `cache/captions/<sha>.json` and survives "
    "Claude Desktop restarts forever.\n\n"
    "Recommended workflow: (1) call `extract_frame_grid(video_path)`, "
    "(2) vision-read the returned image, (3) call this with a 1-2 "
    "sentence `description` plus `tags` (single-word keywords) and an "
    "optional `mood`. Once the b-roll library is captioned, "
    "`search_broll(query)` becomes a one-call lookup instead of a "
    "20-call vision marathon.\n\n"
    "Idempotent: calling again on the same path overwrites the prior "
    "caption (you can refine the description after seeing more frames). "
    "Synchronous."
)

SEARCH_BROLL = (
    "Case-insensitive substring search over cached b-roll captions "
    "(see `describe_clip`). Returns the top N matches against "
    "descriptions, tags, and mood, each with the full caption record "
    "so you can rank without a follow-up call.\n\n"
    "Use this when picking b-roll for a specific A-roll segment: pass "
    "a keyword from the segment's transcript (`'product launch'`, "
    "`'lake'`, `'speaker reaction'`) and get back the matching paths. "
    "Beats vision-reading 30 contact sheets every session.\n\n"
    "Optional `folder_path` scopes the search to captions whose source "
    "file lives under that folder. Synchronous, milliseconds."
)

RENDER_PREVIEW = (
    "Render a low-res MP4 preview of a SequenceSpec so you (or the "
    "user) can sanity-check the cut BEFORE opening Final Cut. Same "
    "spec you'd pass to `generate_fcpxml`, just rendered to 480×270 "
    "H.264 via ffmpeg concat instead of emitting timeline XML.\n\n"
    "v0.8.0 is V1-only (B-roll inserts are noted in the response but "
    "not visually overlaid). The preview is enough to QC pacing, "
    "transcript-content choices, dead air, and overall feel. ~2-5s "
    "to render even on a long cut.\n\n"
    "Call this AFTER you've assembled a candidate SequenceSpec but "
    "BEFORE generate_fcpxml. If the preview looks bad, iterate the "
    "spec; if it looks good, ship it. Synchronous."
)

ADD_HANDLES_TO_SPEC = (
    "Return a copy of a SequenceSpec with `handle_sec` of source-handle "
    "pad on every A-roll and B-roll segment. The cut plays the same "
    "content; the NLE editor gets head/tail frames to fine-tune cuts "
    "in the timeline without re-pulling source media.\n\n"
    "Standard editorial convention is 12-24 frames (0.5-1.0s at 24fps). "
    "Default is 0.5s. Bounds: source_in_sec is clamped at 0, "
    "source_out_sec at the file's actual duration (ffprobed). "
    "timeline_offset_sec on B-roll is preserved — only the source-media "
    "window grows.\n\n"
    "Call this RIGHT BEFORE `generate_fcpxml` — handles make the cut "
    "more editable in FCP / Premiere / Resolve without changing what "
    "the audience sees. Synchronous."
)

LOOKUP_TRANSCRIPT_BY_VIDEO_PATH = (
    "Given an absolute video path, find the cached transcript JSON "
    "for that file (or report `found=False`). Saves the agent from "
    "having to know the cache layout. Fast, filesystem-read only.\n\n"
    "Use this at the start of a fresh chat session: pass the absolute "
    "path of a clip the user mentions, and either get the transcript "
    "back instantly OR get a hint to call `transcribe_video` first. "
    "When multiple cached transcripts exist (different models), the "
    "response returns all matches plus a `best_match` for the one "
    "with the most segments."
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
