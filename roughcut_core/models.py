"""Pydantic v2 data models — the shared contract across roughcut_core.

Every model here is deterministic. AI-derived fields live on no model
because there is no AI in this codebase; the agent assembles
`SequenceSpec` from its own decisions and hands it to `generate_fcpxml`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, NonNegativeFloat


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


class ARollSegment(BaseModel):
    """One contiguous interview clip on V1. Always 'chosen' by definition."""

    source_path: Path
    in_sec: NonNegativeFloat
    out_sec: NonNegativeFloat


class BRollInsert(BaseModel):
    """One b-roll insert on V2 attached to the assembled A-roll timeline."""

    source_path: Path
    clip_in_sec: NonNegativeFloat
    clip_out_sec: NonNegativeFloat
    aroll_offset_sec: NonNegativeFloat


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
