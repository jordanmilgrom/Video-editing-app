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

TRANSCRIBE_FOLDER = (
    "One-call batch transcription: enumerates every video in `folder` "
    "and spawns one transcribe_video job per clip. Skips clips already "
    "cached (returns their transcript_path immediately) and, when "
    "`skip_silent=True` (default), clips whose ffprobe reports no audio "
    "stream — silent b-roll where whisper would just hallucinate.\n\n"
    "Response includes three lists: `cached` (transcripts already on "
    "disk, no work), `silent_skipped` (video-only clips), and `spawned` "
    "(new jobs with their job_ids). Poll each spawned job_id with "
    "check_job_status until status='succeeded'.\n\n"
    "Use this INSTEAD of calling transcribe_video N times. On a 32-clip "
    "folder that replaces 32 tool calls with 1 and lets you see up front "
    "which clips are silent (skip) vs. already-transcribed (skip) vs. "
    "queued (poll). Synchronous but fast — just spawns worker subprocesses. "
    "The transcription itself runs async via the pool.\n\n"
    "`recursive` (default True) walks subdirectories; `language` and "
    "`model` are per-clip settings passed to each spawned transcribe_video."
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

DETECT_SCENES = (
    "Shot / scene boundary detection with a representative frame per shot. "
    "Composes analyze_motion (finds cuts) with frame extraction (one "
    "thumbnail per shot). Returns a list of shots — each with start_sec, "
    "end_sec, and `rep_frame_path` pointing at a cached JPEG on disk.\n\n"
    "Use when you want to SURVEY a long b-roll clip by content: read each "
    "shot's rep_frame_path via `get_clip_thumbnail` (which returns it as "
    "inline image content) and vision-decide which shots to caption or "
    "use. Beats scrubbing through the timeline in an NLE.\n\n"
    "How this differs from `analyze_motion`: analyze_motion returns motion "
    "spans (static / slow_pan / fast_pan / cut) — no visual context. "
    "detect_scenes uses THOSE cut spans as boundaries and adds one "
    "thumbnail per shot so you can see it. If you only need stable-span "
    "info for b-roll picking, use analyze_motion; if you're surveying "
    "unknown footage, use detect_scenes.\n\n"
    "`min_shot_sec` (default 1.0) drops sub-second noise around cut "
    "transitions. `sample_hz` (default 2.0) is analyze_motion's temporal "
    "resolution — bump to 4 for fast-paced footage.\n\n"
    "Cached at `cache/scene-frames/<key>_shot<n>_<ms>.jpg`. Synchronous, "
    "~1-3s per minute of source."
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

RENDER_CUT = (
    "Render a SequenceSpec at delivery quality (H.264/AAC MP4) via ffmpeg "
    "concat. Distinct from `render_preview` — preview is a 480x270 "
    "sanity-check MP4 for fast QC; render_cut is what you hand off to a "
    "client or upload.\n\n"
    "**Presets:** `720p30` (1280×720 CRF 20), `1080p30` (1920×1080 CRF 18, "
    "DEFAULT), `1080p60` (1920×1080 CRF 18 60fps), `4k30` (3840×2160 CRF 20). "
    "Custom sizes via preset params; contact us to add more.\n\n"
    "Runs asynchronously through the worker pool. A 30-minute cut at "
    "1080p30 takes 1-3 minutes to encode on Apple Silicon. Poll "
    "`check_job_status(job_id)` until status='succeeded', then read "
    "`result_summary.output_path` for the delivered file.\n\n"
    "v0.11.0 renders the A-roll spine only (V1). B-roll inserts in the "
    "SequenceSpec are counted in `broll_count_skipped` but not composited "
    "— that's a v0.12+ feature. To ship a cut with b-roll TODAY, generate "
    "the FCPXML/XML/EDL via `generate_fcpxml` and open the timeline in "
    "Final Cut / Premiere / Resolve, then export from there."
) + _ASYNC_NOTE

FIND_AUDIO_SILENCES = (
    "Detect silences by examining the actual audio waveform. ffmpeg "
    "decodes the audio to mono 16 kHz PCM; numpy computes per-50ms RMS "
    "envelope in dBFS; spans below `threshold_db` longer than "
    "`min_silence_sec` are returned.\n\n"
    "This is the audio-side complement to `find_silences` (which is "
    "transcript-gap based). Use both: `find_silences` catches gaps "
    "BETWEEN Whisper segments; `find_audio_silences` catches gaps "
    "INSIDE a segment that Whisper merged through. Typically the second "
    "tool finds 2-5x more silence than the first on real interview "
    "footage.\n\n"
    "Defaults: `min_silence_sec=0.3`, `threshold_db=-40` (typical clean "
    "room tone floor). For noisy field audio bump threshold to -30. "
    "Returns `silences` (list), `total_silence_sec`, `silence_pct`. "
    "Synchronous, ~1-3s per minute of source."
)

DETECT_BREATHS = (
    "Find breaths / lip smacks / inhales between transcribed words: short "
    "(<800ms) low-energy spans that aren't quite silence but aren't speech. "
    "Common between sentences and at the start of takes; cutting them "
    "tightens the pace without removing any words.\n\n"
    "Heuristic: spans BETWEEN two consecutive Whisper Word entries with "
    "duration in [0.15s, 0.8s] and mean RMS strictly between the silence "
    "floor (-55 dBFS) and the speech floor (-30 dBFS). Skips segments "
    "without per-word stamps.\n\n"
    "Pair with `find_audio_silences` for full waveform coverage. Or just "
    "call `tighten_take`, which runs all the detectors at once. "
    "Synchronous, ~1-3s per minute of source (one ffmpeg decode)."
)

DETECT_FALSE_STARTS = (
    "Find interview false starts: 'I- I-I think' / 'the the' / 'well, well'. "
    "Common in real-world interview footage. Whisper transcribes the words "
    "faithfully; this tool surfaces the candidates so the editor cuts the "
    "first attempt(s) and keeps the last clean utterance.\n\n"
    "Pure word-stream pattern matching, no audio. Detects:\n"
    "  1. Single-word repeat ('I I think') — first 'I' is the skip.\n"
    "  2. Stutter (3+ identical) — first N-1 are the skip.\n"
    "  3. 2-word phrase repeat ('the thing the thing is').\n\n"
    "Each hit reports `kept_at_sec` (the surviving utterance's start time) "
    "and a `skip` range covering everything before it. Synchronous, "
    "milliseconds."
)

TIGHTEN_TAKE = (
    "**The fused editorial-tightening pass.** Run every applicable "
    "detector with ONE audio decode and return ONE merged list of "
    "speech-only sub-segments. The agent doesn't need to call "
    "find_silences + find_audio_silences + detect_fillers + detect_breaths "
    "+ detect_false_starts separately and do merge math — this tool does "
    "all of it.\n\n"
    "Input: `video_path` + `transcript_path` + the source-clip range you "
    "were about to drop into an ARollSegment as-is.\n\n"
    "Output: `tight_segments` (drop-in `source_in_sec`/`source_out_sec` "
    "ranges for multiple ARollSegments), `skipped_ranges` (what was cut "
    "and why — each range is tagged with its `sources`), and `stats` "
    "(`time_saved_pct`, per-detector breakdown).\n\n"
    "This is the tool the agent should call BY DEFAULT for every long "
    "A-roll take, replacing the find_silences-only flow from v0.7. "
    "Synchronous, ~1-3s per minute of source.\n\n"
    "Iteration knobs: `min_silence_sec` (lower = more aggressive), "
    "`silence_threshold_db` (-40 default, -30 for noisy field audio), "
    "`min_segment_sec` (0.4 default, bump if cut feels choppy). The three "
    "`include_*` flags let you turn individual detectors off."
)

WATCH_SEGMENT = (
    "**The 'actually watch the clip' tool.** Returns a contact-sheet image "
    "of `num_frames` (default 16) evenly-spaced frames from "
    "`[start_sec, end_sec]` with timecodes overlaid, PLUS the transcript "
    "words inside that window (if `transcript_path` is supplied) PLUS audio "
    "energy stats (min/mean/max dBFS, silence %, speech %) — all bundled "
    "into one tool result.\n\n"
    "Use when you're picking BETWEEN candidate takes / segments, or when "
    "you need to verify a moment delivers what its transcript text promises. "
    "Statistical detectors (find_silences, tighten_take, detect_fillers, "
    "analyze_motion) tell you WHERE words / pauses / motion are; this tool "
    "lets you actually SEE the performance — eye contact, energy, gestures, "
    "micro-expressions — and correlate frames with what was said.\n\n"
    "How this differs from `extract_frame_grid`: extract_frame_grid samples "
    "frames across an ENTIRE clip (good for triage / b-roll captioning). "
    "watch_segment samples frames inside a SPECIFIC time window (good for "
    "take selection). For a 30s segment with 16 frames you get one frame "
    "every ~2s — fine-grained enough to see body language change between "
    "shots.\n\n"
    "Sidecar JSON includes `frame_timestamps_sec` (so you can cite "
    "exact moments), `transcript.text` + `transcript.words` (word-level "
    "stamps inside the window), and `audio_stats` (dBFS envelope summary "
    "so you can tell loud / quiet sections without re-running a detector).\n\n"
    "Synchronous, ~1-3s per call. Output is cached at "
    "`cache/watch-sheets/<key>.jpg` — repeat calls with identical args are "
    "free."
)

INDEX_PROJECT = (
    "One-call project inventory. Enumerates every video in `folder` and "
    "returns a compact entry per clip that composes: ffprobe metadata "
    "(duration, resolution, `has_audio`), transcript status (cached / "
    "silent / missing) with `opening_200_chars` and `top_segments` when "
    "present, and any cached b-roll caption (description, tags, mood).\n\n"
    "Use this AT THE START of any documentary session — it replaces the "
    "old fan-out pattern (list_clips + N × summarize_clip + N × "
    "lookup_transcript + N × read_caption) with one call whose response "
    "fits in a single turn. From there the agent picks which clips are "
    "worth reading in full (`read_transcript`), transcribing "
    "(`transcribe_folder`), or captioning (`extract_frame_grid` → "
    "`describe_clip`).\n\n"
    "Every underlying capability caches its own state, so re-indexing "
    "a folder that hasn't changed is close to free. `include_top_segments` "
    "(default 2) controls how many of the longest transcript segments "
    "are inlined per clip as quick-scan quotes — bump to 5 for deeper "
    "scan, drop to 0 to keep the response minimal. Synchronous, "
    "~10s for a 32-clip folder on first pass; ms after cache warm."
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
