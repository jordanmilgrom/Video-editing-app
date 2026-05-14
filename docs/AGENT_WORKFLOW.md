# Agent workflow: typical documentary rough-cut

The 10-step shape of a real session, written so a fresh chat agent can
follow it as a script. Each step lists the tool call and the field it
expects in the response.

```
0. get_project_paths()                       → interview_folder, broll_folder
                                               cache_dir
1. prewarm_model(model_name='large-v3')      → fire-and-forget; no need
                                               to wait. Skip if 'small'
                                               is enough for the shoot.
2. list_clips(folder=interview_folder)       → data.clip_paths[]
3. lookup_transcript_by_video_path(path=X)   → found=False ⇒ call (4)
                                               found=True  ⇒ skip to (5)
4. for clip in clip_paths:                   ≤4 concurrent (the worker
     transcribe_video(video_path=clip,         pool caps the rest into a
                      language='en')           queue with queue_position
                                               reported on each spawn).
   Poll each job_id with check_job_status
   until status='succeeded'.
5. for transcript in transcripts:            cheap, ms-scale. Use this
     summarize_clip(transcript_path=X)         to triage which clips
                                               are interesting.
6. read_transcript(transcript_path=X,        full-fidelity read for
                   start_segment=0)            the keepers. Paginate via
                                               next_start if has_more.
7. (optional) cluster_takes_by_silence(
       transcript_path=X)                    → data.preview shows the
                                               top 3 clusters inline.
8. Build the spine: pick A-roll segments,    SequenceSpec field names:
   pair with B-roll where it strengthens       source_in_sec, source_out_sec,
   the cut.                                    timeline_offset_sec.
                                               (Old names in_sec / clip_in_sec
                                                still work via aliases.)
9. generate_fcpxml(                          → emits .fcpxml + .xml + .edl
       sequence_spec={ … },                    + .relink.csv, self-validates,
       output_path='/abs/cut.fcpxml')          returns import_hints per NLE.
10. validate_fcpxml(fcpxml_path=X)            confirm before handing off.
                                               (already run internally; only
                                               call this directly when
                                               debugging an existing file.)
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
