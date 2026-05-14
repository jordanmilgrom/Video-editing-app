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
