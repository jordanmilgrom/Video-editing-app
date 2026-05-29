"""Detect interview false starts: "I- I- I think" / "the the" / "well, well".

Common in real-world interview footage. Whisper transcribes the words
faithfully, but the editor wants to cut the first attempt(s) and keep
only the last clean utterance. This module surfaces those candidates
as ranges-to-skip the agent can drop into a tight-cut SequenceSpec.

The heuristic is pattern-based on the word stream, no audio analysis:

  1. **Single-word stutter**: same lowercase-normalized word repeats
     within `max_gap_sec`. e.g. "I I think" or "the- the".
  2. **Phrase repeat**: same 2-word phrase repeats within `max_gap_sec`
     and the SECOND occurrence is clean (no further repeat).
     e.g. "the thing- the thing is".
  3. **Stuttered start**: 3+ identical single-letter or 2-letter
     tokens with hyphens or punctuation, e.g. "I-I-I" rendered by
     Whisper as separate words.

When a pattern matches, we mark every occurrence EXCEPT THE LAST as a
"false start" — that's the conservative choice. The agent gets a
`keep` field pointing at the surviving utterance and a `skip` range
covering everything before it.

Pure transcript analysis — no audio probing. Mirrors `fillers.py` in
output shape so the agent can treat the lists interchangeably.
"""

from __future__ import annotations

import re
from pathlib import Path

from roughcut_core.models import Segment, Transcript, Word

DEFAULT_MAX_GAP_SEC = 2.0    # max time between a false start and its retry
DEFAULT_MIN_REPEAT = 2       # minimum occurrences to call it a false start

_NORMALIZE_RE = re.compile(r"^\W+|\W+$")


def detect_false_starts(
    transcript_path: Path,
    start_sec: float | None = None,
    end_sec: float | None = None,
    max_gap_sec: float = DEFAULT_MAX_GAP_SEC,
    min_repeat: int = DEFAULT_MIN_REPEAT,
) -> dict:
    """Return false-start ranges inside [start_sec, end_sec] of a transcript.

    Returns:
        {
          "transcript_path": str,
          "source_video": str,
          "range": [start_sec, end_sec],
          "false_starts": [
            {"start_sec", "end_sec", "duration_sec", "pattern",
             "phrase", "occurrences", "kept_at_sec", "surrounding_text"},
            ...
          ],
          "total_false_start_sec": float,
        }
    """
    t = Transcript.model_validate_json(
        Path(transcript_path).read_text(encoding="utf-8")
    )
    lo = 0.0 if start_sec is None else float(start_sec)
    hi = t.duration if end_sec is None else float(end_sec)

    ranges: list[dict] = []
    for seg in t.segments:
        if seg.end <= lo or seg.start >= hi or not seg.words:
            continue
        ranges.extend(_scan_segment(seg, lo, hi, max_gap_sec, min_repeat))

    # Sort by start_sec; dedupe overlapping ranges (longer wins).
    ranges.sort(key=lambda r: (r["start_sec"], -r["duration_sec"]))
    deduped: list[dict] = []
    for r in ranges:
        if deduped and r["start_sec"] < deduped[-1]["end_sec"]:
            continue
        deduped.append(r)

    return {
        "transcript_path": str(transcript_path),
        "source_video": str(t.source_path),
        "range": [round(lo, 3), round(hi, 3)],
        "max_gap_sec": max_gap_sec,
        "min_repeat": min_repeat,
        "false_starts": deduped,
        "total_false_start_sec": round(
            sum(r["duration_sec"] for r in deduped), 3
        ),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub("", text).lower()


def _scan_segment(
    seg: Segment, lo: float, hi: float, max_gap_sec: float, min_repeat: int,
) -> list[dict]:
    """Find false-start candidates inside one Whisper segment."""
    words = seg.words
    norm = [_normalize(w.text) for w in words]
    hits: list[dict] = []

    # --- Pattern 1 + 3: single-word repeat (stutter when 3+) ---------------
    i = 0
    while i < len(norm):
        if not norm[i]:
            i += 1
            continue
        run_start = i
        run = [i]
        j = i + 1
        while j < len(norm) and norm[j] == norm[i]:
            if words[j].start - words[run[-1]].end > max_gap_sec:
                break
            run.append(j)
            j += 1
        if len(run) >= min_repeat:
            # Each of run[:-1] is a false start; the last one is kept.
            first_w = words[run[0]]
            keep_w = words[run[-1]]
            if first_w.end > lo and first_w.start < hi:
                hits.append(_hit(
                    pattern="single_word_stutter" if len(run) >= 3 else "single_word_repeat",
                    phrase=words[run[0]].text,
                    start=first_w.start,
                    end=keep_w.start,
                    occurrences=len(run),
                    kept_at_sec=keep_w.start,
                    seg=seg, word_idx_start=run[0], word_idx_end=run[-2],
                ))
        i = j if len(run) > 1 else i + 1

    # --- Pattern 2: 2-word phrase repeat ---------------------------------
    seen_phrase_end: int = -1
    for k in range(len(norm) - 1):
        if k <= seen_phrase_end:
            continue
        if not norm[k] or not norm[k + 1]:
            continue
        phrase = (norm[k], norm[k + 1])
        # Look ahead within max_gap_sec to find a duplicate phrase.
        m = k + 2
        while m < len(norm) - 1:
            gap = words[m].start - words[k + 1].end
            if gap > max_gap_sec:
                break
            if (norm[m], norm[m + 1]) == phrase:
                first_w = words[k]
                keep_w = words[m]
                if first_w.end > lo and first_w.start < hi:
                    hits.append(_hit(
                        pattern="phrase_repeat",
                        phrase=f"{words[k].text} {words[k+1].text}",
                        start=first_w.start,
                        end=keep_w.start,
                        occurrences=2,
                        kept_at_sec=keep_w.start,
                        seg=seg, word_idx_start=k, word_idx_end=k + 1,
                    ))
                seen_phrase_end = m + 1
                break
            m += 1

    return hits


def _hit(
    pattern: str, phrase: str, start: float, end: float,
    occurrences: int, kept_at_sec: float,
    seg: Segment, word_idx_start: int, word_idx_end: int,
) -> dict:
    ctx_start = max(0, word_idx_start - 2)
    ctx_end = min(len(seg.words), word_idx_end + 4)  # +1 for kept word + 2 context
    surrounding = " ".join(w.text for w in seg.words[ctx_start:ctx_end])
    return {
        "start_sec": round(start, 3),
        "end_sec": round(end, 3),
        "duration_sec": round(end - start, 3),
        "pattern": pattern,
        "phrase": phrase,
        "occurrences": occurrences,
        "kept_at_sec": round(kept_at_sec, 3),
        "surrounding_text": surrounding,
    }
