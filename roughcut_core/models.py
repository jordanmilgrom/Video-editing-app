"""Pydantic v2 data models — the shared contract across roughcut_core.

Every model here is deterministic. AI-derived fields live on no model
because there is no AI in this codebase; the agent assembles
`SequenceSpec` from its own decisions and hands it to `generate_fcpxml`.

v0.6.5 unified the cut-spec field names:

  ARollSegment.in_sec        → source_in_sec        (in source clip)
  ARollSegment.out_sec       → source_out_sec
  BRollInsert.clip_in_sec    → source_in_sec        (in source clip)
  BRollInsert.clip_out_sec   → source_out_sec
  BRollInsert.aroll_offset_sec → timeline_offset_sec (on the assembled timeline)

Old names are still accepted as validation aliases — passing
`{"in_sec": 0.0, "out_sec": 5.0}` continues to work. A DeprecationWarning
is emitted so callers can migrate without breaking.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, NonNegativeFloat, model_validator


class Word(BaseModel):
    text: str
    start: NonNegativeFloat
    end: NonNegativeFloat


class Segment(BaseModel):
    text: str
    start: NonNegativeFloat
    end: NonNegativeFloat
    words: list[Word] = Field(default_factory=list)


class Transcript(BaseModel):
    source_path: Path
    source_hash: str
    duration: NonNegativeFloat
    language: str | None = None
    segments: list[Segment] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()


class TakeCluster(BaseModel):
    """A contiguous run of segments separated from its neighbors by silence.

    The agent decides which cluster is the best read; this object only
    describes what was spoken and when.
    """

    cluster_id: str
    segment_indices: list[int]
    segments: list[Segment] = Field(default_factory=list)
    in_sec: NonNegativeFloat
    out_sec: NonNegativeFloat
    text: str


class ScriptAlignment(BaseModel):
    """Candidate transcript segments matching one script line.

    Confidence is parallel to `candidate_segment_indices` and sorted
    descending. The agent picks which candidate(s) to use.
    """

    line_index: int
    line_text: str
    candidate_segment_indices: list[int] = Field(default_factory=list)
    candidate_segments: list[Segment] = Field(default_factory=list)
    confidence: list[float] = Field(default_factory=list)


class ClipMeta(BaseModel):
    """Deterministic ffprobe-derived metadata for a clip on disk."""

    path: Path
    duration_sec: NonNegativeFloat
    fps: float
    width: int
    height: int
    codec: str
    size_bytes: int


# ---------------------------------------------------------------------------
# Final timeline contract. Built by the agent; consumed by fcpxml.write_fcpxml.
# ---------------------------------------------------------------------------


_LEGACY_AROLL_KEYS = {"in_sec": "source_in_sec", "out_sec": "source_out_sec"}
_LEGACY_BROLL_KEYS = {
    "clip_in_sec": "source_in_sec",
    "clip_out_sec": "source_out_sec",
    "aroll_offset_sec": "timeline_offset_sec",
}


def _warn_legacy(model_name: str, mapping: dict[str, str], data: dict[str, Any]) -> None:
    """Emit a DeprecationWarning for each legacy key seen in `data`."""
    legacy_used = [k for k in mapping if k in data]
    if legacy_used:
        for k in legacy_used:
            warnings.warn(
                f"{model_name}: field '{k}' is deprecated; "
                f"use '{mapping[k]}' instead. Both names continue to work in v0.6.5.",
                DeprecationWarning, stacklevel=3,
            )


class ARollSegment(BaseModel):
    """One contiguous interview clip on V1. Always 'chosen' by definition.

    Canonical fields are `source_in_sec` / `source_out_sec` (where in the
    source clip). The pre-v0.6.5 names `in_sec` / `out_sec` are accepted
    as aliases for back-compat; a DeprecationWarning fires on first use.
    """

    model_config = ConfigDict(populate_by_name=True)

    source_path: Path
    source_in_sec: NonNegativeFloat = Field(
        validation_alias=AliasChoices("source_in_sec", "in_sec"),
    )
    source_out_sec: NonNegativeFloat = Field(
        validation_alias=AliasChoices("source_out_sec", "out_sec"),
    )

    @model_validator(mode="before")
    @classmethod
    def _warn_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _warn_legacy("ARollSegment", _LEGACY_AROLL_KEYS, data)
        return data

    # ----- back-compat property accessors (will be removed in v0.7) ---------

    @property
    def in_sec(self) -> NonNegativeFloat:
        return self.source_in_sec

    @property
    def out_sec(self) -> NonNegativeFloat:
        return self.source_out_sec


class BRollInsert(BaseModel):
    """One b-roll insert on V2 attached to the assembled A-roll timeline.

    Canonical fields:
      - `source_in_sec` / `source_out_sec` — where in the source clip
      - `timeline_offset_sec` — where on the assembled timeline

    The pre-v0.6.5 names `clip_in_sec` / `clip_out_sec` / `aroll_offset_sec`
    are accepted as aliases with a DeprecationWarning.
    """

    model_config = ConfigDict(populate_by_name=True)

    source_path: Path
    source_in_sec: NonNegativeFloat = Field(
        validation_alias=AliasChoices("source_in_sec", "clip_in_sec"),
    )
    source_out_sec: NonNegativeFloat = Field(
        validation_alias=AliasChoices("source_out_sec", "clip_out_sec"),
    )
    timeline_offset_sec: NonNegativeFloat = Field(
        validation_alias=AliasChoices("timeline_offset_sec", "aroll_offset_sec"),
    )

    @model_validator(mode="before")
    @classmethod
    def _warn_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _warn_legacy("BRollInsert", _LEGACY_BROLL_KEYS, data)
        return data

    # ----- back-compat property accessors (will be removed in v0.7) ---------

    @property
    def clip_in_sec(self) -> NonNegativeFloat:
        return self.source_in_sec

    @property
    def clip_out_sec(self) -> NonNegativeFloat:
        return self.source_out_sec

    @property
    def aroll_offset_sec(self) -> NonNegativeFloat:
        return self.timeline_offset_sec


class SequenceSpec(BaseModel):
    """Everything `generate_fcpxml` needs to emit a Premiere/Resolve FCPXML.

    The agent assembles this from its take and b-roll decisions. A-roll
    segments are laid out contiguously on the spine in list order;
    b-roll inserts are placed by `aroll_offset_sec` (relative to the
    start of the assembled A-roll, not to any individual segment).
    """

    name: str = "roughcut"
    fps: float = 23.976
    width: int = 1920
    height: int = 1080
    aroll: list[ARollSegment]
    broll: list[BRollInsert] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Multicam mode (podcast / multi-camera, parallel recordings)
# ---------------------------------------------------------------------------


class MulticamGroup(BaseModel):
    """A set of clips that were rolling simultaneously, with discovered offsets.

    `offsets_sec[i]` is the time of `clip_paths[i]` relative to the
    earliest clip in the group (which has offset 0.0). So if camera B
    started recording 3.2s after camera A, A's offset is 0.0 and B's is
    3.2 (or the agent can interpret this as "B's t=0 happens at A's
    t=3.2"). Confidence is a 0..1 measure of how clean the audio-sync
    cross-correlation was.
    """

    group_id: str
    clip_paths: list[Path]
    offsets_sec: list[float]
    confidence: float = 1.0


class SpeakerLabel(BaseModel):
    """Per-segment speaker assignment for a multicam transcript."""

    segment_idx: int
    speaker_idx: int          # index into the group's clip list
    speaker_label: str        # e.g. "Speaker 1" or user-supplied
    rms_per_clip: list[float] # raw mic RMS readings, parallel to clip list


class AngleSelection(BaseModel):
    """One angle (one camera) covering one slice of the assembled timeline."""

    timeline_in_sec: NonNegativeFloat
    timeline_out_sec: NonNegativeFloat
    clip_path: Path
    clip_in_sec: NonNegativeFloat
    clip_out_sec: NonNegativeFloat
    reason: str = ""          # e.g. "primary" or "reaction"
