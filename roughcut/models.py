"""Pydantic v2 data models — the contract shared by every module."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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
    """A full transcript for a single source media file."""

    source_path: Path
    source_hash: str
    duration: NonNegativeFloat
    language: str | None = None
    segments: list[Segment] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()


class Take(BaseModel):
    """One selected take from an interview file."""

    source_path: Path
    source_hash: str
    in_sec: NonNegativeFloat
    out_sec: NonNegativeFloat
    text: str
    cluster_id: str
    chosen: bool = True
    reason: str = ""


class Clip(BaseModel):
    """A b-roll clip with Claude-derived metadata."""

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
    """A b-roll insert anchored to a sentence in the A-roll."""

    sentence_index: int
    sentence_text: str
    clip_hash: str
    clip_in_sec: NonNegativeFloat
    clip_out_sec: NonNegativeFloat
    aroll_offset_sec: NonNegativeFloat
    reason: str = ""


class Sequence(BaseModel):
    """The final assembled timeline, ready for FCPXML emission."""

    name: str = "roughcut"
    frame_rate: float = 23.976
    width: int = 1920
    height: int = 1080
    takes: list[Take] = Field(default_factory=list)
    broll: list[BrollMatch] = Field(default_factory=list)
    clips: dict[str, Clip] = Field(default_factory=dict)


class IngestSpec(BaseModel):
    """CLI inputs, normalized."""

    interview_dir: Path
    broll_dir: Path
    output_path: Path
    script_path: Path | None = None
    cache_dir: Path = Path(".roughcut-cache")
    mode: Literal["auto", "script"] = "auto"
