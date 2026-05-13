"""Semantic A-roll → b-roll matcher.

Splits the chosen A-roll (concatenation of chosen takes) into sentences,
distributes each sentence's timing proportionally inside its take, and
asks Claude — once per sentence — for 0..3 b-roll inserts from the clip
library. Each returned `BrollMatch` carries an absolute `aroll_offset_sec`
so the FCPXML stage can drop the insert at the right beat on V2.
"""

from __future__ import annotations

import json
import re

from roughcut import claude
from roughcut.models import BrollMatch, Clip, Take

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "clip_hash": {"type": "string"},
                    "clip_in_sec": {"type": "number"},
                    "clip_out_sec": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["clip_hash", "clip_in_sec", "clip_out_sec"],
            },
        }
    },
    "required": ["picks"],
}


def match(takes: list[Take], clips: dict[str, Clip]) -> list[BrollMatch]:
    chosen = [tk for tk in takes if tk.chosen]
    if not chosen or not clips:
        return []
    library = _format_library(clips)
    matches: list[BrollMatch] = []
    sentence_index = 0
    aroll_cursor = 0.0
    for take in chosen:
        for s, _, text in _time_sentences(_split_sentences(take.text), take.in_sec, take.out_sec):
            offset = aroll_cursor + (s - take.in_sec)
            for pick in _ask(text, library, clips):
                matches.append(BrollMatch(
                    sentence_index=sentence_index,
                    sentence_text=text,
                    clip_hash=pick["clip_hash"],
                    clip_in_sec=float(pick["clip_in_sec"]),
                    clip_out_sec=float(pick["clip_out_sec"]),
                    aroll_offset_sec=offset,
                    reason=str(pick.get("reason", "")),
                ))
            sentence_index += 1
        aroll_cursor += take.out_sec - take.in_sec
    return matches


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text.strip()) if p.strip()]
    if parts:
        return parts
    return [text.strip()] if text.strip() else []


def _time_sentences(
    sentences: list[str], in_sec: float, out_sec: float
) -> list[tuple[float, float, str]]:
    """Distribute (in_sec, out_sec) across sentences proportionally by length."""
    if not sentences:
        return []
    duration = max(0.0, out_sec - in_sec)
    total_chars = sum(len(s) for s in sentences) or 1
    out: list[tuple[float, float, str]] = []
    cursor = in_sec
    for s in sentences:
        seg_dur = duration * len(s) / total_chars
        out.append((cursor, cursor + seg_dur, s))
        cursor += seg_dur
    return out


def _format_library(clips: dict[str, Clip]) -> str:
    items = [
        {
            "clip_hash": c.source_hash,
            "subject": c.subject,
            "motion": c.motion,
            "mood": c.mood,
            "tags": c.tags,
            "duration": round(c.duration, 2),
            "suggested_in_sec": round(c.suggested_in_sec, 2),
            "suggested_out_sec": round(c.suggested_out_sec, 2),
            "description": c.description,
        }
        for c in clips.values()
    ]
    return json.dumps(items, indent=2)


def _ask(sentence_text: str, library: str, clips: dict[str, Clip]) -> list[dict]:
    result = claude.call(
        "match_broll", _SCHEMA, tool_name="match_broll",
        sentence=sentence_text, library=library,
    )
    picks = list(result.get("picks", []))
    valid: list[dict] = []
    for pick in picks:
        clip = clips.get(str(pick.get("clip_hash", "")))
        if clip is None:
            continue
        cin = max(0.0, float(pick.get("clip_in_sec", 0.0)))
        cout = min(clip.duration, float(pick.get("clip_out_sec", clip.duration)))
        if cout <= cin:
            continue
        pick["clip_in_sec"] = cin
        pick["clip_out_sec"] = cout
        valid.append(pick)
    return valid
