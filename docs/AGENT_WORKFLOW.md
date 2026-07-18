# Agent workflow: typical documentary rough-cut

The 14-step shape of a real session, written so a fresh chat agent can
follow it as a script. Each step lists the tool call and the field it
expects in the response. v0.9.0 collapsed the multi-detector A-roll
tightening pass into a single `tighten_take` call; v0.8.0 added the
captioning + preview + handles passes. Older clients can skip them but
quality drops noticeably.

```
0.  get_project_paths()                       → interview_folder, broll_folder
                                                cache_dir
1.  prewarm_model(model_name='large-v3')      → fire-and-forget; no need
                                                to wait. Skip if 'small'
                                                is enough for the shoot.
2.  index_project(folder=interview_folder)    → v0.11.0: ONE call replaces
    → per-clip: duration, has_audio,            steps 3-5 for the vast
      transcript status, opening_200_chars,     majority of shoots. Only
      top_segments, caption (if any)            fall back to (3-5) if you
                                                need per-clip precision.
3.  transcribe_folder(                         → v0.11.0: replaces the
      folder=interview_folder,                    per-clip fan-out. Skips
      language='en', model='large-v3')            silent b-roll + cached
    → cached: already-done transcripts          hits. Returns job_ids for
      spawned: new job_ids to poll              each new transcription.
      silent_skipped: video-only clips
4.  poll each spawned job_id via check_job_status until succeeded.
5.  (implicit — index_project already surfaces summaries for cached clips).
6.  read_transcript(transcript_path=X,        full-fidelity read for
                    start_segment=0)            the keepers. Paginate via
                                                next_start if has_more.

6b. watch_segment(video_path=X,                v0.10.0: when picking
                  start_sec=A, end_sec=B,        between candidate takes
                  transcript_path=T)             or verifying a moment
    → contact sheet (16 frames) + transcript    delivers what its
      words in window + audio energy stats.     transcript text promises.
    → The "actually watch the clip" tool —      Statistical detectors tell
      use when statistical detectors aren't     you WHERE things are;
      enough and you need to SEE the take.      watch_segment lets you SEE.

──── B-roll captioning (one-time per clip, cached forever) ────

7a. detect_scenes(video_path=X)                v0.11.0: for LONG b-roll
    → shots + rep_frame_path per shot           clips (>10s), survey the
    → get_clip_thumbnail(rep_frame_path)        content by looking at one
      to vision-read each shot's content        rep frame per shot. Cheaper
                                                than extract_frame_grid on
                                                a whole clip.
7b. for clip in broll_folder:                  Check cache first; only
      extract_frame_grid(video_path=clip)        caption clips that don't
      → vision-read the returned image          already have a description.
      describe_clip(video_path=clip,
                    description='<1-2 sentences>',
                    tags=[...], mood='...')

──── Building the spine (A-roll) ────

8.  for each long take you want to use:        v0.9.0: ONE call replaces
      tighten_take(                              the v0.8 manual merge of
        video_path=clip,                         find_silences + detect_fillers.
        transcript_path=X,
        start_sec=A, end_sec=B)
    → Returns `tight_segments` — drop-in source_in/out_sec ranges for
      multiple ARollSegments. Also `stats.time_saved_pct` so you can
      iterate measurably. Typically 25-40% shorter than the raw take.
    → If you need the components separately (rare): find_silences (gaps
      between Whisper segments), find_audio_silences (waveform RMS,
      catches gaps INSIDE segments), detect_fillers (um/uh/like),
      detect_breaths (low-energy spans between words), detect_false_starts
      ("I- I- I think").

──── Picking B-roll ────

9.  for each A-roll segment that needs       semantic search instead of
    visual support:                           vision-reading 30 contact
      search_broll(query='<keyword from        sheets every session.
                   the segment's text>')      analyze_motion before
      analyze_motion(video_path=X)             selecting source_in/out_sec
    → Choose B-roll source_in/out_sec inside  inside framing-stable
      stable_spans (static + slow_pan, ≥2s).  spans only.

──── Assembling the cut ────

10. Build the SequenceSpec:                   field names (canonical):
    aroll = [ARollSegment(...)] × N             source_in_sec,
    broll = [BRollInsert(...)] × M              source_out_sec,
                                                timeline_offset_sec

11. render_preview(sequence_spec={...},       sanity-check pacing before
                   output_path='/abs/p.mp4')   committing. Read the MP4
                                                back as inline image to
                                                vision-QC. Iterate on the
                                                spec if it feels wrong.

12. add_handles_to_spec(sequence_spec={...},  pad source_in/out by 0.5s
                        handle_sec=0.5)        so editors can nudge in
                                                the NLE. Skip if you want
                                                a frame-precise cut.

13. generate_fcpxml(                          → v0.11.0: emits .fcpxml +
        sequence_spec={ … },                    .xml + .edl + .otio +
        output_path='/abs/cut.fcpxml')          .relink.csv, self-validates,
                                                returns import_hints per NLE.

14. validate_fcpxml(fcpxml_path=X)            confirm before handing off.
                                                (already run internally;
                                                only call this directly
                                                when debugging an existing
                                                file.)

15. render_cut(                                v0.11.0 OPTIONAL: render an
        sequence_spec={ … },                    MP4 deliverable at 720p30
        output_path='/abs/cut.mp4',             / 1080p30 / 1080p60 / 4k30.
        preset='1080p30')                       Async — poll job_id. Use
    → job_id → poll → succeeded                 when you want a self-
      → result_summary.output_path              contained file, not an NLE
                                                project.
```

## Concurrency contract

- Worker pool default: 4. Override with `ROUGHCUT_WORKER_POOL_SIZE`.
- Idle timeout default: 900 s (15 min). Override with
  `ROUGHCUT_WORKER_IDLE_SECONDS` if you're transcribing in a tight loop
  and don't want the workers to exit on you.
- Jobs beyond pool size sit in the queue; each spawn returns
  `queue_position` so the agent can predict latency.
- `restart_workers()` is the heavy hammer if a worker wedges.
- `get_server_logs(tail=100)` shows recent worker / watchdog activity
  if you need to diagnose without bothering the user.

## Naming consistency (v0.6.5+)

Fields that describe positions inside a source clip:
`source_in_sec`, `source_out_sec`.
Fields that describe positions on the assembled timeline:
`timeline_offset_sec` (B-roll) or `timeline_in_sec`/`timeline_out_sec`
(multicam angles).

Pre-v0.6.5 names (`in_sec`, `out_sec`, `clip_in_sec`, `clip_out_sec`,
`aroll_offset_sec`) are accepted as aliases — both forms work, the new
name is canonical going forward.

## Response shape

Every tool returns `data` (rich result dict) and optionally
`next_steps` (suggested follow-up calls with example args). On
failure: `ok=False`, `error` (stable code), `message` (free-form).

The legacy `summary` field is still populated as a mirror of `data`
so agents trained on the old shape keep working through v0.6.
