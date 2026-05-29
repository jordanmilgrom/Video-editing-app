# Agent workflow: typical documentary rough-cut

The 14-step shape of a real session, written so a fresh chat agent can
follow it as a script. Each step lists the tool call and the field it
expects in the response. v0.8.0 added the editorial-intelligence steps
6-9 (transcript tightening + b-roll caching) and steps 12-13 (preview +
handles) — older clients can skip them but quality drops noticeably.

```
0.  get_project_paths()                       → interview_folder, broll_folder
                                                cache_dir
1.  prewarm_model(model_name='large-v3')      → fire-and-forget; no need
                                                to wait. Skip if 'small'
                                                is enough for the shoot.
2.  list_clips(folder=interview_folder)       → data.clip_paths[]
3.  lookup_transcript_by_video_path(path=X)   → found=False ⇒ call (4)
                                                found=True  ⇒ skip to (5)
4.  for clip in clip_paths:                   ≤4 concurrent (the worker
      transcribe_video(video_path=clip,         pool caps the rest into a
                       language='en')           queue with queue_position
                                                reported on each spawn).
    Poll each job_id with check_job_status
    until status='succeeded'.
5.  for transcript in transcripts:            cheap, ms-scale. Use this
      summarize_clip(transcript_path=X)         to triage which clips
                                                are interesting.
6.  read_transcript(transcript_path=X,        full-fidelity read for
                    start_segment=0)            the keepers. Paginate via
                                                next_start if has_more.

──── B-roll captioning (one-time per clip, cached forever) ────

7.  for clip in broll_folder:                  Check cache first; only
      extract_frame_grid(video_path=clip)        caption clips that don't
      → vision-read the returned image          already have a description.
      describe_clip(video_path=clip,
                    description='<1-2 sentences>',
                    tags=[...], mood='...')

──── Building the spine (A-roll) ────

8.  for each long take you want to use:        v0.8.0: tighten BEFORE
      find_silences(transcript_path=X,           you commit to a take's
                    start_sec=A, end_sec=B)       raw start/end.
      detect_fillers(transcript_path=X,
                     start_sec=A, end_sec=B)
    → Merge `silences` + `filler_ranges`; the inverse is your tight
      list of ARollSegment ranges. Typically 15-30% shorter than raw.

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

13. generate_fcpxml(                          → emits .fcpxml + .xml + .edl
        sequence_spec={ … },                    + .relink.csv, self-validates,
        output_path='/abs/cut.fcpxml')          returns import_hints per NLE.

14. validate_fcpxml(fcpxml_path=X)            confirm before handing off.
                                                (already run internally;
                                                only call this directly
                                                when debugging an existing
                                                file.)
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
