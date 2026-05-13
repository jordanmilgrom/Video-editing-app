"""Take detection and best-take selection.

Two clustering strategies:

- **With script**: fuzzy-match each transcript segment against script lines;
  group all segments that map to the same line. Multiple readings of the
  same line are therefore clustered together.
- **No script**: split the transcript by silence gaps > SILENCE_GAP, then
  ask Claude to cluster phrases that read the same content.

For each cluster, split contiguous runs into variants (one variant per
take), then ask Claude to pick the cleanest variant. Every alternate take
is still returned (with `chosen=False`) so the editor can see what was
rejected.
"""

from __future__ import annotations

import difflib
import json

from roughcut import claude
from roughcut.models import Segment, Take, Transcript

SILENCE_GAP = 2.0
SCRIPT_MATCH_THRESHOLD = 0.55


def detect_takes(transcript: Transcript, script_text: str | None = None) -> list[Take]:
    """Return Takes for one transcript. Chosen takes have `chosen=True`."""
    if script_text and script_text.strip():
        clusters = _cluster_by_script(transcript, script_text)
    else:
        clusters = _cluster_by_silence_and_claude(transcript)
    out: list[Take] = []
    for i, cluster in enumerate(clusters):
        out.extend(_pick_best(transcript, cluster, f"c{i}"))
    return out


def _phrase_text(segments: list[Segment]) -> str:
    return " ".join(s.text.strip() for s in segments).strip()


def _best_line(text: str, lines: list[str]) -> int | None:
    """Return the index of the best-matching script line, or None."""
    target = text.lower().strip()
    if not target:
        return None
    best_idx: int | None = None
    best_score = SCRIPT_MATCH_THRESHOLD
    for i, line in enumerate(lines):
        score = difflib.SequenceMatcher(None, target, line.lower()).ratio()
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx


def _cluster_by_script(transcript: Transcript, script_text: str) -> list[list[Segment]]:
    lines = [ln.strip() for ln in script_text.splitlines() if ln.strip()]
    if not lines:
        return _cluster_by_silence_and_claude(transcript)
    by_line: dict[int, list[Segment]] = {}
    for seg in transcript.segments:
        idx = _best_line(seg.text, lines)
        if idx is not None:
            by_line.setdefault(idx, []).append(seg)
    return [by_line[k] for k in sorted(by_line)]


def _split_variants(segments: list[Segment], gap: float = SILENCE_GAP) -> list[list[Segment]]:
    """Split segments into contiguous runs (one variant per take attempt)."""
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: s.start)
    variants: list[list[Segment]] = [[ordered[0]]]
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.start - prev.end >= gap:
            variants.append([cur])
        else:
            variants[-1].append(cur)
    return variants


def _cluster_by_silence_and_claude(transcript: Transcript) -> list[list[Segment]]:
    if not transcript.segments:
        return []
    phrases = _split_variants(transcript.segments)
    if len(phrases) <= 1:
        return phrases
    payload = [{"index": i, "text": _phrase_text(p)} for i, p in enumerate(phrases)]
    schema = {
        "type": "object",
        "properties": {
            "clusters": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "integer"}},
                "description": "List of clusters; each cluster is a list of phrase indices.",
            }
        },
        "required": ["clusters"],
    }
    result = claude.call(
        "cluster_takes",
        schema,
        tool_name="cluster_takes",
        segments=json.dumps(payload, indent=2),
    )
    clusters: list[list[Segment]] = []
    for indices in result.get("clusters", []):
        flat = [seg for i in indices if 0 <= i < len(phrases) for seg in phrases[i]]
        if flat:
            clusters.append(flat)
    return clusters or phrases


def _pick_best(transcript: Transcript, cluster: list[Segment], cluster_id: str) -> list[Take]:
    variants = _split_variants(cluster)
    if not variants:
        return []
    if len(variants) == 1:
        v = variants[0]
        return [_take(transcript, v, cluster_id, chosen=True, reason="only variant")]
    payload = [
        {"index": i, "in_sec": round(v[0].start, 3), "out_sec": round(v[-1].end, 3), "text": _phrase_text(v)}
        for i, v in enumerate(variants)
    ]
    schema = {
        "type": "object",
        "properties": {
            "chosen_index": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["chosen_index"],
    }
    result = claude.call(
        "pick_best_take",
        schema,
        tool_name="select_take",
        cluster=json.dumps(payload, indent=2),
    )
    chosen_idx = int(result.get("chosen_index", 0))
    reason = str(result.get("reason", ""))
    return [
        _take(
            transcript, v, cluster_id,
            chosen=(i == chosen_idx),
            reason=reason if i == chosen_idx else "",
        )
        for i, v in enumerate(variants)
    ]


def _take(transcript: Transcript, variant: list[Segment], cluster_id: str, *, chosen: bool, reason: str) -> Take:
    return Take(
        source_path=transcript.source_path,
        source_hash=transcript.source_hash,
        in_sec=variant[0].start,
        out_sec=variant[-1].end,
        text=_phrase_text(variant),
        cluster_id=cluster_id,
        chosen=chosen,
        reason=reason,
    )
