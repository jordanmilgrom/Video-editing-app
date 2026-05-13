"""Take primitives: silence clustering and script alignment.

Deterministic. No LLM calls. These functions surface structural views of
the transcript so the agent can decide which take is best (or which
alignment to trust). The decisions themselves live in the agent.
"""

from __future__ import annotations

import difflib

from roughcut_core.models import ScriptAlignment, Segment, TakeCluster, Transcript

DEFAULT_SILENCE_THRESHOLD = 2.0
DEFAULT_MATCH_THRESHOLD = 0.55


def cluster_by_silence(
    transcript: Transcript,
    silence_threshold_sec: float = DEFAULT_SILENCE_THRESHOLD,
) -> list[TakeCluster]:
    """Group transcript segments into clusters separated by silence.

    A new cluster starts whenever the gap from the previous segment's
    end to the next segment's start meets or exceeds
    `silence_threshold_sec`. Segments are processed in start-time order;
    `segment_indices` preserves the position of each segment in the
    original `transcript.segments` list.
    """
    if not transcript.segments:
        return []
    indexed = sorted(enumerate(transcript.segments), key=lambda x: x[1].start)
    clusters: list[list[tuple[int, Segment]]] = [[indexed[0]]]
    for (_, prev), (idx, cur) in zip(indexed, indexed[1:]):
        if cur.start - prev.end >= silence_threshold_sec:
            clusters.append([(idx, cur)])
        else:
            clusters[-1].append((idx, cur))
    return [_make_cluster(i, c) for i, c in enumerate(clusters)]


def align_to_script(transcript: Transcript, script_text: str) -> list[ScriptAlignment]:
    """For each script line, surface candidate segments with confidence.

    Confidence is the difflib `SequenceMatcher.ratio()` between the
    lowercased segment text and the lowercased script line. Candidates
    are returned sorted descending by confidence. Lines with no
    candidate above `DEFAULT_MATCH_THRESHOLD` are still returned, just
    with empty candidate lists.
    """
    lines = [ln.strip() for ln in script_text.splitlines() if ln.strip()]
    out: list[ScriptAlignment] = []
    for line_idx, line in enumerate(lines):
        target = line.lower()
        scored: list[tuple[int, float, Segment]] = []
        for seg_idx, seg in enumerate(transcript.segments):
            ratio = difflib.SequenceMatcher(None, seg.text.lower().strip(), target).ratio()
            if ratio >= DEFAULT_MATCH_THRESHOLD:
                scored.append((seg_idx, ratio, seg))
        scored.sort(key=lambda x: -x[1])
        out.append(ScriptAlignment(
            line_index=line_idx,
            line_text=line,
            candidate_segment_indices=[s[0] for s in scored],
            candidate_segments=[s[2] for s in scored],
            confidence=[s[1] for s in scored],
        ))
    return out


def _make_cluster(idx: int, items: list[tuple[int, Segment]]) -> TakeCluster:
    segs = [s for _, s in items]
    return TakeCluster(
        cluster_id=f"c{idx}",
        segment_indices=[i for i, _ in items],
        segments=segs,
        in_sec=segs[0].start,
        out_sec=segs[-1].end,
        text=" ".join(s.text.strip() for s in segs).strip(),
    )


# ---------------------------------------------------------------------------
# Internal helpers preserved for callers (and tests) that depend on them.
# ---------------------------------------------------------------------------


def _split_variants(
    segments: list[Segment], gap: float = DEFAULT_SILENCE_THRESHOLD
) -> list[list[Segment]]:
    """Split a list of segments into contiguous runs (silence-bounded)."""
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


def _best_line(
    text: str, lines: list[str], threshold: float = DEFAULT_MATCH_THRESHOLD
) -> int | None:
    """Index of the closest script line by difflib ratio, or None."""
    target = text.lower().strip()
    if not target:
        return None
    best_idx: int | None = None
    best_score = threshold
    for i, line in enumerate(lines):
        score = difflib.SequenceMatcher(None, target, line.lower()).ratio()
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx
