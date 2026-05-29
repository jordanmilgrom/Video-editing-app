"""Filler-word detection — the Descript-style flagship feature.

Whisper transcripts already carry word-level timestamps on each segment
(`Segment.words: list[Word]`, with `text/start/end` per word). v0.6.x
never exposed them; v0.8.0 turns them into actionable edit cuts.

`detect_fillers` scans the word stream for "um" / "uh" / "you know" /
"like" / "I mean" / "kind of" / "sort of" / "right?" / "actually" /
"basically" and returns each occurrence as a `(start_sec, end_sec)`
range. The agent then includes those ranges in its silence list when
building speech_sub_segments — the resulting cut is dramatically tighter
without dropping any meaningful content.

Detection is regex-based, case-insensitive, with whole-word boundaries
to avoid clipping "umbrella" or "like-minded". Multi-word patterns
("you know", "I mean", "kind of", "sort of") match across adjacent
Word entries by walking a sliding window over the segment's words.

The output shape mirrors `find_silences.silences[]` so the agent can
treat fillers and silences interchangeably when assembling tight cuts.
"""

from __future__ import annotations

import re
from pathlib import Path

from roughcut_core.models import Segment, Transcript, Word

# Default English filler patterns. Multi-word entries match across
# adjacent Word entries (sliding window). Single-word entries match a
# single Word's `text` (with optional surrounding punctuation).
#
# Keep this list conservative — every false positive removes real
# content. "right" without a question mark is too common to drop;
# "actually" / "basically" are common enough that we include them but
# the agent can override via the `patterns` arg.
DEFAULT_FILLER_PATTERNS: tuple[str, ...] = (
    "um",
    "uh",
    "uhh",
    "umm",
    "er",
    "you know",
    "i mean",
    "kind of",
    "sort of",
    "like",
    "actually",
    "basically",
    "literally",
)


def detect_fillers(
    transcript_path: Path,
    start_sec: float | None = None,
    end_sec: float | None = None,
    patterns: tuple[str, ...] | None = None,
) -> dict:
    """Return filler-word ranges inside [start_sec, end_sec] of a transcript.

    `patterns` overrides the default English list. Pass a tuple of
    lowercased phrases; single tokens match one Word, multi-token
    phrases match a sliding window across adjacent Words. If `start_sec`
    or `end_sec` are None, scans the whole transcript.

    Returns:
        {
          "transcript_path": str,
          "source_video": str,
          "range": [start_sec, end_sec],
          "patterns_used": [...],
          "filler_ranges": [
            {"start_sec", "end_sec", "duration_sec", "text",
             "pattern", "surrounding_text"},
            ...
          ],
          "total_filler_sec": float,
          "filler_words_per_minute": float,
          "scanned_duration_sec": float,
        }
    """
    t = Transcript.model_validate_json(
        Path(transcript_path).read_text(encoding="utf-8")
    )
    pats = tuple(p.lower() for p in (patterns or DEFAULT_FILLER_PATTERNS))
    lo = 0.0 if start_sec is None else float(start_sec)
    hi = t.duration if end_sec is None else float(end_sec)

    # Pre-split patterns into single vs multi-word; multi-word need a
    # sliding window across adjacent Word entries.
    single_word_pats = {p for p in pats if " " not in p}
    multi_word_pats = [p.split() for p in pats if " " in p]

    ranges: list[dict] = []
    for seg_idx, seg in enumerate(t.segments):
        if seg.end <= lo or seg.start >= hi:
            continue
        if not seg.words:
            # Some Whisper outputs omit per-word stamps; skip those segments.
            continue
        for hit in _scan_segment(seg, single_word_pats, multi_word_pats, lo, hi):
            hit["segment_idx"] = seg_idx
            ranges.append(hit)

    scanned = max(0.0, hi - lo)
    total = sum(r["duration_sec"] for r in ranges)
    rate = (len(ranges) / scanned * 60.0) if scanned > 0 else 0.0

    return {
        "transcript_path": str(transcript_path),
        "source_video": str(t.source_path),
        "range": [round(lo, 3), round(hi, 3)],
        "patterns_used": list(pats),
        "filler_ranges": ranges,
        "total_filler_sec": round(total, 3),
        "filler_words_per_minute": round(rate, 2),
        "scanned_duration_sec": round(scanned, 3),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

# Strip leading / trailing punctuation, lowercase. "Um," -> "um".
_NORMALIZE_RE = re.compile(r"^\W+|\W+$")


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub("", text).lower()


def _scan_segment(
    seg: Segment,
    single_pats: set[str],
    multi_pats: list[list[str]],
    lo: float,
    hi: float,
) -> list[dict]:
    """Find filler hits inside `seg`. Returns one dict per hit."""
    words = seg.words
    normalized = [_normalize(w.text) for w in words]
    hits: list[dict] = []

    # Single-word matches.
    for i, w_norm in enumerate(normalized):
        if w_norm in single_pats:
            w = words[i]
            if w.end <= lo or w.start >= hi:
                continue
            hits.append(_hit(w.start, w.end, w.text, w_norm, seg, i, i))

    # Multi-word sliding-window matches.
    for pat_tokens in multi_pats:
        plen = len(pat_tokens)
        for i in range(len(normalized) - plen + 1):
            if normalized[i : i + plen] == pat_tokens:
                w_start = words[i]
                w_end = words[i + plen - 1]
                if w_end.end <= lo or w_start.start >= hi:
                    continue
                pat_str = " ".join(pat_tokens)
                phrase = " ".join(w.text for w in words[i : i + plen])
                hits.append(_hit(
                    w_start.start, w_end.end, phrase, pat_str,
                    seg, i, i + plen - 1,
                ))

    # Sort by start, dedupe overlapping (multi-word phrase wins over
    # its constituent single-word hits — e.g. "you know" wins over
    # just "you" / "know" if either were in the pattern set).
    hits.sort(key=lambda h: (h["start_sec"], -h["duration_sec"]))
    deduped: list[dict] = []
    for h in hits:
        if deduped and h["start_sec"] < deduped[-1]["end_sec"]:
            continue
        deduped.append(h)
    return deduped


def _hit(
    start: float, end: float, text: str, pattern: str,
    seg: Segment, word_idx_start: int, word_idx_end: int,
) -> dict:
    """One filler-word hit with a small surrounding-text window for context."""
    # Surrounding context: 2 words on either side, clamped to the segment.
    ctx_start = max(0, word_idx_start - 2)
    ctx_end = min(len(seg.words), word_idx_end + 3)
    surrounding = " ".join(w.text for w in seg.words[ctx_start:ctx_end])
    return {
        "start_sec": round(start, 3),
        "end_sec": round(end, 3),
        "duration_sec": round(end - start, 3),
        "text": text,
        "pattern": pattern,
        "surrounding_text": surrounding,
    }
