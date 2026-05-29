"""Source-handle frames: pad every aroll segment so editors can nudge in the NLE.

Today a roughcut SequenceSpec is frame-precise to the cut points. That's
correct, but it leaves the NLE editor no slack: there's no way to nudge
a cut a few frames earlier or later without re-pulling source media.
Standard editorial practice is to leave 12-24 frame handles on either
side of every cut so the editor can fine-tune in the timeline.

`add_handles_to_spec(spec, handle_sec=0.5)` returns a NEW SequenceSpec
with every A-roll segment's `source_in_sec` reduced by `handle_sec` and
`source_out_sec` increased by `handle_sec`, clamped at 0 and the file's
actual duration (ffprobed best-effort). The cut still plays the same
content — only the source-media window grows.

We do this as an explicit agent-callable mutation rather than a hidden
`generate_fcpxml` parameter so the agent can decide per-cut whether
handles are wanted (rare cases like tight back-to-back cuts inside
the same source range don't want them).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roughcut_core.models import ARollSegment, BRollInsert, SequenceSpec

# 12 frames at 24fps = 0.5s. Standard NLE convention.
DEFAULT_HANDLE_SEC = 0.5


def add_handles_to_spec(
    spec: SequenceSpec, handle_sec: float = DEFAULT_HANDLE_SEC,
) -> SequenceSpec:
    """Return a copy of `spec` with `handle_sec` of source-handle pad on each
    A-roll and B-roll segment.

    Bounds:
      - `source_in_sec` is clamped at 0 (can't seek before file start).
      - `source_out_sec` is clamped at the file's actual duration if
        ffprobe succeeds; otherwise left alone (the bounds-validation
        layer in `generate_fcpxml` will catch true overruns).

    Timeline placement is preserved — `timeline_offset_sec` on B-roll
    isn't changed, and the A-roll spine still concatenates in the same
    order.
    """
    if handle_sec < 0:
        raise ValueError(f"handle_sec must be >= 0, got {handle_sec}")

    durations = _probe_durations(spec)

    new_aroll = [
        _pad_aroll(seg, handle_sec, durations.get(_key(seg.source_path)))
        for seg in spec.aroll
    ]
    new_broll = [
        _pad_broll(ins, handle_sec, durations.get(_key(ins.source_path)))
        for ins in spec.broll
    ]

    return spec.model_copy(update={"aroll": new_aroll, "broll": new_broll})


def _pad_aroll(
    seg: ARollSegment, handle_sec: float, file_duration: float | None,
) -> ARollSegment:
    new_in = max(0.0, seg.source_in_sec - handle_sec)
    new_out = seg.source_out_sec + handle_sec
    if file_duration is not None and new_out > file_duration:
        new_out = file_duration
    # Defensively make sure we didn't invert.
    if new_out <= new_in:
        return seg
    return ARollSegment(
        source_path=seg.source_path,
        source_in_sec=new_in,
        source_out_sec=new_out,
    )


def _pad_broll(
    ins: BRollInsert, handle_sec: float, file_duration: float | None,
) -> BRollInsert:
    new_in = max(0.0, ins.source_in_sec - handle_sec)
    new_out = ins.source_out_sec + handle_sec
    if file_duration is not None and new_out > file_duration:
        new_out = file_duration
    if new_out <= new_in:
        return ins
    return BRollInsert(
        source_path=ins.source_path,
        source_in_sec=new_in,
        source_out_sec=new_out,
        timeline_offset_sec=ins.timeline_offset_sec,
    )


def _probe_durations(spec: SequenceSpec) -> dict[Path, float]:
    """ffprobe each unique source. Silent on failure (best-effort)."""
    from roughcut_core import clips
    paths: set[Path] = set()
    for seg in spec.aroll:
        paths.add(_key(seg.source_path))
    for ins in spec.broll:
        paths.add(_key(ins.source_path))
    out: dict[Path, float] = {}
    for p in paths:
        try:
            meta = clips.probe_clip(p)
        except Exception:  # noqa: BLE001
            continue
        if meta.duration_sec > 0:
            out[p] = meta.duration_sec
    return out


def _key(p: Path | str) -> Path:
    """Mirror of fcpxml._resolve_for_dedup so handle bounds match the emitter."""
    return Path(p).resolve(strict=False)
