"""The fused editorial-tightening pass.

v0.7-0.8 shipped the components: `documentary.find_silences` (transcript
gaps), `fillers.detect_fillers` (word-stream patterns), `audio.find_audio_silences`
(waveform RMS), `audio.detect_breaths` (inter-word low energy),
`false_starts.detect_false_starts` (word repetition). Each is useful in
isolation, but the agent had to call ≥3 tools and merge overlapping
ranges by hand to actually get one tight cut.

`tighten_take(video, transcript, start, end)` runs every applicable
detector with one audio decode, fuses their outputs into a single
sorted list of speech-only sub-segments, and reports `time_saved_sec`
so the agent can iterate measurably.

This is the tool the agent should call by default for every long take.
It supersedes (but doesn't replace) the individual detectors.

Pipeline:

  1. Decode audio envelope ONCE.
  2. Run all detectors against [start_sec, end_sec], passing the
     envelope into the audio-side detectors so they don't re-decode.
  3. Union the "skip" ranges (silences + audio silences + fillers +
     breaths + false starts).
  4. Invert the union against [start_sec, end_sec] to get the speech-only
     `tight_segments`.
  5. Drop tight_segments shorter than `min_segment_sec` (default 0.4s).
  6. Aggregate stats.

Each sub-segment in the returned `tight_segments` has the canonical
SequenceSpec field names (`source_in_sec`, `source_out_sec`) so the
agent can drop them straight into an ARollSegment list.
"""

from __future__ import annotations

from pathlib import Path

from roughcut_core import audio, documentary, false_starts, fillers
from roughcut_core.models import Transcript

DEFAULT_MIN_SEGMENT_SEC = 0.4   # below this is choppy / sub-syllabic


def tighten_take(
    video_path: Path,
    transcript_path: Path,
    start_sec: float,
    end_sec: float,
    min_silence_sec: float = 0.3,
    silence_threshold_db: float = -40.0,
    min_segment_sec: float = DEFAULT_MIN_SEGMENT_SEC,
    include_fillers: bool = True,
    include_breaths: bool = True,
    include_false_starts: bool = True,
    filler_patterns: tuple[str, ...] | None = None,
) -> dict:
    """Run every editorial detector once and return one tight segment list.

    Args:
        video_path: Source video file (for waveform analysis).
        transcript_path: The cached Whisper transcript JSON for `video_path`.
        start_sec, end_sec: The take's source range, e.g. the one you
            were about to drop into an ARollSegment as-is.
        min_silence_sec: Below this, gaps are normal inter-syllabic
            pauses, not silence to cut.
        silence_threshold_db: RMS dBFS threshold for waveform silence.
            -40 is a typical clean-room floor; bump higher (-30) for
            noisy field recordings.
        min_segment_sec: Drop any tight sub-segment shorter than this.
            0.4s keeps the cut from getting choppy.
        include_fillers / include_breaths / include_false_starts:
            Toggle individual detectors. Default all on.
        filler_patterns: Override the default English filler list.

    Returns:
        {
          "video_path", "transcript_path", "range",
          "tight_segments": [
            {"source_in_sec", "source_out_sec", "duration_sec"},
            ...
          ],
          "skipped_ranges": [
            {"start_sec", "end_sec", "duration_sec",
             "sources": ["silence", "filler", ...]},
            ...
          ],
          "stats": {
            "original_duration_sec", "tightened_duration_sec",
            "total_skipped_sec", "time_saved_pct",
            "silence_sec_transcript", "silence_sec_audio",
            "filler_sec", "breath_sec", "false_start_sec",
          },
        }
    """
    if end_sec <= start_sec:
        raise ValueError(f"end_sec ({end_sec}) must be > start_sec ({start_sec})")

    # 1. Decode audio envelope once (shared by silence + breath detectors).
    envelope = audio.decode_audio_envelope(Path(video_path))

    # 2. Run all detectors against the requested range.
    silences_tx = documentary.find_silences(
        Path(transcript_path), start_sec, end_sec,
        min_silence_sec=min_silence_sec,
    )
    silences_audio = audio.find_audio_silences(
        Path(video_path),
        min_silence_sec=min_silence_sec,
        threshold_db=silence_threshold_db,
        start_sec=start_sec, end_sec=end_sec,
        envelope=envelope,
    )
    filler_data = (
        fillers.detect_fillers(
            Path(transcript_path),
            start_sec=start_sec, end_sec=end_sec,
            patterns=filler_patterns,
        )
        if include_fillers
        else {"filler_ranges": [], "total_filler_sec": 0.0}
    )
    breath_data = (
        audio.detect_breaths(
            Path(video_path), Path(transcript_path),
            start_sec=start_sec, end_sec=end_sec,
            envelope=envelope,
        )
        if include_breaths
        else {"breaths": [], "total_breath_sec": 0.0}
    )
    false_start_data = (
        false_starts.detect_false_starts(
            Path(transcript_path), start_sec=start_sec, end_sec=end_sec,
        )
        if include_false_starts
        else {"false_starts": [], "total_false_start_sec": 0.0}
    )

    # 3. Collect every (start, end, source-label) range.
    labeled: list[tuple[float, float, str]] = []
    for s in silences_tx["silences"]:
        labeled.append((s["start_sec"], s["end_sec"], "silence_transcript"))
    for s in silences_audio["silences"]:
        labeled.append((s["start_sec"], s["end_sec"], "silence_audio"))
    for f in filler_data["filler_ranges"]:
        labeled.append((f["start_sec"], f["end_sec"], "filler"))
    for b in breath_data["breaths"]:
        labeled.append((b["start_sec"], b["end_sec"], "breath"))
    for r in false_start_data["false_starts"]:
        labeled.append((r["start_sec"], r["end_sec"], "false_start"))

    # 4. Merge overlapping ranges, recording which sources contributed.
    skipped_ranges = _merge_overlapping(labeled, start_sec, end_sec)

    # 5. Invert to get speech-only sub-segments. Drop sub-segments
    #    shorter than min_segment_sec.
    tight_segments = _invert(skipped_ranges, start_sec, end_sec, min_segment_sec)

    # 6. Stats.
    original = end_sec - start_sec
    tightened = sum(s["duration_sec"] for s in tight_segments)
    skipped = sum(s["duration_sec"] for s in skipped_ranges)

    return {
        "video_path": str(video_path),
        "transcript_path": str(transcript_path),
        "range": [round(start_sec, 3), round(end_sec, 3)],
        "tight_segments": tight_segments,
        "skipped_ranges": skipped_ranges,
        "stats": {
            "original_duration_sec": round(original, 3),
            "tightened_duration_sec": round(tightened, 3),
            "total_skipped_sec": round(skipped, 3),
            "time_saved_pct": round(100.0 * skipped / original, 1) if original > 0 else 0.0,
            "silence_sec_transcript": silences_tx["total_silence_sec"],
            "silence_sec_audio": silences_audio["total_silence_sec"],
            "filler_sec": filler_data.get("total_filler_sec", 0.0),
            "breath_sec": breath_data.get("total_breath_sec", 0.0),
            "false_start_sec": false_start_data.get("total_false_start_sec", 0.0),
            "tight_segment_count": len(tight_segments),
            "skipped_range_count": len(skipped_ranges),
        },
        "include": {
            "fillers": include_fillers,
            "breaths": include_breaths,
            "false_starts": include_false_starts,
        },
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _merge_overlapping(
    labeled: list[tuple[float, float, str]],
    bound_lo: float, bound_hi: float,
) -> list[dict]:
    """Merge overlapping (start, end, source) into [{start, end, sources[]}].

    Clamps every range to [bound_lo, bound_hi] before merging.
    """
    if not labeled:
        return []
    clamped: list[tuple[float, float, str]] = []
    for s, e, src in labeled:
        s2 = max(bound_lo, s)
        e2 = min(bound_hi, e)
        if e2 > s2:
            clamped.append((s2, e2, src))
    clamped.sort()

    merged: list[dict] = []
    cur_start, cur_end, cur_sources = clamped[0][0], clamped[0][1], {clamped[0][2]}
    for s, e, src in clamped[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
            cur_sources.add(src)
        else:
            merged.append({
                "start_sec": round(cur_start, 3),
                "end_sec": round(cur_end, 3),
                "duration_sec": round(cur_end - cur_start, 3),
                "sources": sorted(cur_sources),
            })
            cur_start, cur_end, cur_sources = s, e, {src}
    merged.append({
        "start_sec": round(cur_start, 3),
        "end_sec": round(cur_end, 3),
        "duration_sec": round(cur_end - cur_start, 3),
        "sources": sorted(cur_sources),
    })
    return merged


def _invert(
    skipped: list[dict], bound_lo: float, bound_hi: float, min_segment_sec: float,
) -> list[dict]:
    """Return the complement of `skipped` inside [bound_lo, bound_hi]."""
    tight: list[dict] = []
    cursor = bound_lo
    for s in skipped:
        if s["start_sec"] > cursor:
            length = s["start_sec"] - cursor
            if length >= min_segment_sec:
                tight.append({
                    "source_in_sec": round(cursor, 3),
                    "source_out_sec": round(s["start_sec"], 3),
                    "duration_sec": round(length, 3),
                })
        cursor = s["end_sec"]
    if bound_hi > cursor:
        length = bound_hi - cursor
        if length >= min_segment_sec:
            tight.append({
                "source_in_sec": round(cursor, 3),
                "source_out_sec": round(bound_hi, 3),
                "duration_sec": round(length, 3),
            })
    return tight
