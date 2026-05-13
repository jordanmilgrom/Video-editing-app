"""Pydantic v2 data models — the shared contract across roughcut_core.

Deterministic primitives only. No AI-derived fields on new models.
`Take` / `Clip` / `BrollMatch` / `Sequence` are retained for now because
`fcpxml.py` still consumes them; they will be folded into `SequenceSpec`
in Phase C.
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
# Legacy models still consumed by fcpxml.py. Slated for Phase C consolidation
# into a single `SequenceSpec` provided by the agent.
# ---------------------------------------------------------------------------


class Take(BaseModel):
    source_path: Path
    source_hash: str
    in_sec: NonNegativeFloat
    out_sec: NonNegativeFloat
    text: str
    cluster_id: str
    chosen: bool = True
    reason: str = ""


class Clip(BaseModel):
    source_path: Path
    source_hash: str
    duration: NonNegativeFloat
    subject: str = ""
    motion: str = ""
    mood: str = ""
    tags: list[str] = Field(default_factory=list)
    suggested_in_sec: NonNegativeFloat = 0.0
    suggested_out_sec: NonNegativeFloat = 0.0
    description: str = ""


class BrollMatch(BaseModel):
    sentence_index: int
    sentence_text: str
    clip_hash: str
    clip_in_sec: NonNegativeFloat
    clip_out_sec: NonNegativeFloat
    aroll_offset_sec: NonNegativeFloat
    reason: str = ""


class Sequence(BaseModel):
    name: str = "roughcut"
    frame_rate: float = 23.976
    width: int = 1920
    height: int = 1080
    takes: list[Take] = Field(default_factory=list)
    broll: list[BrollMatch] = Field(default_factory=list)
    clips: dict[str, Clip] = Field(default_factory=dict)
