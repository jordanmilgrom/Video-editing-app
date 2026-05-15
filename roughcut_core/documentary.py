"""Documentary / unscripted mode primitives.

The current size-bounded tool returns cap an `transcribe_video` result
at ~200 chars. Great for the agent's own reasoning, terrible for a
documentary editor who needs to know *what was actually said* across
32 clips of a trade-show shoot.

Three deterministic, no-LLM helpers fix that:

  - `read_transcript(path, start=0, end=None, max_chars=900_000)` pages
    through a saved transcript JSON. Returns segments + their timecodes,
    capped at ~900 KB so we stay under Desktop's 1 MB tool-result limit.

  - `search_transcripts(query, folder=None, max_results=20, context=2)`
    case-insensitive substring scan across every transcript in the
    cache (or scoped to a folder of clips). Returns hit segments with
    a few segments of context on either side.

  - `summarize_clip(path)` produces a deterministic snippet view —
    opening 200 chars, closing 200 chars, longest continuous segment,
    total speech chars — so the agent can scan many clips fast before
    deciding which to read in full.

Stdlib + roughcut_core.models only. No heavy imports.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roughcut_core.models import Transcript

# Stay safely below Claude Desktop's 1 MB tool-result cap.
DEFAULT_READ_CAP_CHARS = 900_000


def read_transcript(
    transcript_path: Path,
    start_segment: int = 0,
    end_segment: int | None = None,
    max_chars: int = DEFAULT_READ_CAP_CHARS,
) -> dict:
    """Page through a saved transcript JSON.

    Returns a dict the MCP layer can wrap straight into a ToolResponse
    summary. `has_more` / `next_start` let the agent loop until the
    full transcript has been read.
    """
    t = Transcript.model_validate_json(Path(transcript_path).read_text(encoding="utf-8"))
    total = len(t.segments)
    start = max(0, int(start_segment))
    stop_at = total if end_segment is None else min(total, int(end_segment))

    out: list[dict] = []
    chars = 0
    idx = start
    while idx < stop_at:
        seg = t.segments[idx]
        # Rough size budget: char count of text + a small per-segment overhead
        # for the timecode fields. 60 chars/segment for the JSON envelope.
        seg_chars = len(seg.text) + 60
        if out and chars + seg_chars > max_chars:
            break
        out.append({
            "idx": idx,
            "start_sec": round(seg.start, 3),
            "end_sec": round(seg.end, 3),
            "text": seg.text,
        })
        chars += seg_chars
        idx += 1

    has_more = idx < total
    return {
        "transcript_path": str(transcript_path),
        "source_video": str(t.source_path),
        "duration_sec": round(t.duration, 3),
        "language": t.language,
        "total_segments": total,
        "returned_range": [start, idx],
        "segments": out,
        "has_more": has_more,
        "next_start": idx if has_more else None,
        "char_count": chars,
    }


def search_transcripts(
    query: str,
    cache_dir: Path,
    folder_path: Path | None = None,
    max_results: int = 20,
    context_segments: int = 2,
) -> dict:
    """Case-insensitive substring search across every cached transcript.

    If `folder_path` is provided, only hits whose `Transcript.source_path`
    lives inside that folder are returned. Each hit includes a few
    segments of context on either side so the agent can read the moment
    without paging.
    """
    needle = (query or "").lower()
    transcripts_root = Path(cache_dir) / "transcripts"
    hits: list[dict] = []
    files_searched = 0
    if needle and transcripts_root.is_dir():
        for path in sorted(transcripts_root.rglob("*.json")):
            try:
                t = Transcript.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            files_searched += 1
            if folder_path and not _under(t.source_path, folder_path):
                continue
            for seg_idx, seg in enumerate(t.segments):
                if needle in seg.text.lower():
                    hits.append(_hit(path, t, seg_idx, context_segments))
                    if len(hits) >= max_results:
                        break
            if len(hits) >= max_results:
                break
    return {
        "query": query,
        "folder_path": str(folder_path) if folder_path else None,
        "files_searched": files_searched,
        "result_count": len(hits),
        "results": hits,
    }


def lookup_transcript_by_video_path(video_path: Path, cache_dir: Path) -> dict:
    """Find the cached transcript JSON for a given source video.

    Scans every transcript under `cache_dir/transcripts/**/*.json` and
    returns the first one whose `source_path` resolves to the same file
    as `video_path`. v0.6.5 P2 #12: the agent should never have to
    guess the cache layout — pass the absolute video path you started
    with and get back the corresponding transcript path.

    Returns `{"found": False, "video_path": ...}` if no match.
    """
    target = Path(video_path).resolve(strict=False)
    transcripts_root = Path(cache_dir) / "transcripts"
    if not transcripts_root.is_dir():
        return {"found": False, "video_path": str(target),
                "reason": "no_transcripts_cached"}
    candidates: list[dict] = []
    for path in sorted(transcripts_root.rglob("*.json")):
        try:
            t = Transcript.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if Path(t.source_path).resolve(strict=False) == target:
            candidates.append({
                "transcript_path": str(path),
                "model_dir": path.parent.name,
                "segment_count": len(t.segments),
                "duration_sec": round(t.duration, 3),
                "language": t.language,
            })
    if not candidates:
        return {"found": False, "video_path": str(target),
                "reason": "no_match",
                "hint": "Run transcribe_video(video_path=...) first."}
    return {
        "found": True,
        "video_path": str(target),
        "match_count": len(candidates),
        "matches": candidates,
        # Convenience: prefer the largest-model transcript when several
        # exist (more accurate). Agents that want all can read `matches`.
        "best_match": max(candidates, key=lambda c: c["segment_count"]),
    }


def find_silences(
    transcript_path: Path,
    start_sec: float,
    end_sec: float,
    min_silence_sec: float = 0.5,
) -> dict:
    """Return silence ranges and tight speech sub-segments inside a take.

    v0.7.0 P0: a documentary cut shouldn't include 30 seconds of "uhh"
    pauses just because they fell inside the chosen take. Call this on
    every long aroll segment, then build sub-segments around the
    silences instead of using the raw range.

    Pure transcript analysis — no audio probing. We treat the gap
    between consecutive transcript segments as silence; gaps >=
    `min_silence_sec` are returned. The leading gap (start_sec to
    first segment) and trailing gap (last segment to end_sec) are
    tagged as `head` / `tail` silences.

    `speech_sub_segments` is the inverse: the spans inside [start_sec,
    end_sec] that contain speech with no big pauses. Each sub-segment
    is at least 0.3s long. Drop these into a SequenceSpec as multiple
    ARollSegments to get a tight cut.
    """
    t = Transcript.model_validate_json(
        Path(transcript_path).read_text(encoding="utf-8")
    )
    segs = [s for s in t.segments if s.end > start_sec and s.start < end_sec]
    silences: list[dict] = []

    if segs:
        head_gap = segs[0].start - start_sec
        if head_gap >= min_silence_sec:
            silences.append({
                "start_sec": round(start_sec, 3),
                "end_sec": round(segs[0].start, 3),
                "duration_sec": round(head_gap, 3),
                "kind": "head",
            })
        for i in range(len(segs) - 1):
            gap_start = segs[i].end
            gap_end = segs[i + 1].start
            if gap_end - gap_start >= min_silence_sec:
                silences.append({
                    "start_sec": round(gap_start, 3),
                    "end_sec": round(gap_end, 3),
                    "duration_sec": round(gap_end - gap_start, 3),
                    "kind": "interior",
                })
        tail_gap = end_sec - segs[-1].end
        if tail_gap >= min_silence_sec:
            silences.append({
                "start_sec": round(segs[-1].end, 3),
                "end_sec": round(end_sec, 3),
                "duration_sec": round(tail_gap, 3),
                "kind": "tail",
            })

    # Inverse: speech-only sub-segments. Drop sub-segments shorter than
    # 0.3s — too short to bother editing.
    sub_segments: list[dict] = []
    cursor = start_sec
    for sil in silences:
        if sil["start_sec"] > cursor + 0.3:
            sub_segments.append({
                "source_in_sec": round(cursor, 3),
                "source_out_sec": round(sil["start_sec"], 3),
                "duration_sec": round(sil["start_sec"] - cursor, 3),
            })
        cursor = sil["end_sec"]
    if end_sec > cursor + 0.3:
        sub_segments.append({
            "source_in_sec": round(cursor, 3),
            "source_out_sec": round(end_sec, 3),
            "duration_sec": round(end_sec - cursor, 3),
        })

    return {
        "transcript_path": str(transcript_path),
        "source_video": str(t.source_path),
        "range": [round(start_sec, 3), round(end_sec, 3)],
        "min_silence_sec": min_silence_sec,
        "silences": silences,
        "speech_sub_segments": sub_segments,
        "total_silence_sec": round(
            sum(s["duration_sec"] for s in silences), 3
        ),
        "tightened_duration_sec": round(
            sum(s["duration_sec"] for s in sub_segments), 3
        ),
        "original_duration_sec": round(end_sec - start_sec, 3),
    }


def summarize_clip(transcript_path: Path) -> dict:
    """Deterministic per-clip snippet view — no LLM call.

    Lets the agent scan 32 clips' "shape" in a few hundred KB of total
    tool output before deciding which one to `read_transcript` in full.
    """
    t = Transcript.model_validate_json(Path(transcript_path).read_text(encoding="utf-8"))
    text_full = " ".join(s.text.strip() for s in t.segments).strip()
    longest_seg_idx = 0
    longest_chars = 0
    for i, seg in enumerate(t.segments):
        n = len(seg.text)
        if n > longest_chars:
            longest_chars = n
            longest_seg_idx = i
    longest_seg = t.segments[longest_seg_idx] if t.segments else None
    return {
        "transcript_path": str(transcript_path),
        "filename": Path(t.source_path).name,
        "duration_sec": round(t.duration, 3),
        "segment_count": len(t.segments),
        "total_speech_chars": len(text_full),
        "opening_200_chars": text_full[:200],
        "closing_200_chars": text_full[-200:] if len(text_full) > 200 else "",
        "longest_continuous_segment_chars": longest_chars,
        "longest_segment_text": longest_seg.text if longest_seg else "",
        "longest_segment_start_sec": round(longest_seg.start, 3) if longest_seg else None,
        "longest_segment_end_sec": round(longest_seg.end, 3) if longest_seg else None,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _hit(path: Path, t: Transcript, seg_idx: int, context: int) -> dict:
    """One search hit + a small window of surrounding segments."""
    before = []
    for i in range(max(0, seg_idx - context), seg_idx):
        s = t.segments[i]
        before.append({"idx": i, "start_sec": round(s.start, 3), "text": s.text})
    after = []
    for i in range(seg_idx + 1, min(len(t.segments), seg_idx + 1 + context)):
        s = t.segments[i]
        after.append({"idx": i, "start_sec": round(s.start, 3), "text": s.text})
    hit = t.segments[seg_idx]
    return {
        "transcript_path": str(path),
        "source_video": str(t.source_path),
        "segment_idx": seg_idx,
        "start_sec": round(hit.start, 3),
        "end_sec": round(hit.end, 3),
        "matched_text": hit.text,
        "context_before": before,
        "context_after": after,
    }


def _under(child: Path | str, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False
