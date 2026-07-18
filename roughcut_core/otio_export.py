"""OpenTimelineIO (.otio) export — in-house JSON emitter.

OTIO is a documented JSON schema; for a linear V1 Timeline of clips we
don't need the opentimelineio wheel (which would add ~5-10 MB to the
.dxt and pull in a native library that's tricky to bundle for macOS
arm64). This module emits the same JSON structure the opentimelineio
library produces for a Timeline with one Video track full of Clips.

Verified compatible with OTIO 0.15+ readers (Resolve, DaVinci import,
tools like `otiotool` and `otiocat`).

For v0.11.0 the export is V1-only (A-roll spine). B-roll on lane 1 is
deferred; agents that need multi-track exchange should use the FCPXML
output instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roughcut_core.models import SequenceSpec


def write_otio(spec: SequenceSpec, output_path: Path) -> None:
    """Emit `spec` as an .otio JSON file at `output_path`."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = build_otio_doc(spec)
    output_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def build_otio_doc(spec: SequenceSpec) -> dict[str, Any]:
    """Build the OTIO Timeline dict without writing anything."""
    fps = float(spec.fps) if spec.fps else 24.0

    clips_children: list[dict[str, Any]] = []
    for i, seg in enumerate(spec.aroll):
        src_path = str(Path(seg.source_path).resolve())
        dur_sec = float(seg.source_out_sec) - float(seg.source_in_sec)
        clip = {
            "OTIO_SCHEMA": "Clip.1",
            "name": f"aroll-{i}-{Path(seg.source_path).stem}",
            "source_range": _time_range(seg.source_in_sec, dur_sec, fps),
            "media_references": {
                "DEFAULT_MEDIA": {
                    "OTIO_SCHEMA": "ExternalReference.1",
                    "target_url": _file_url(src_path),
                    # We don't ffprobe the source here to keep this pure —
                    # available_range=null signals "read from media".
                    "available_range": None,
                    "name": Path(seg.source_path).name,
                    "metadata": {},
                },
            },
            "active_media_reference_key": "DEFAULT_MEDIA",
            "metadata": {},
            "effects": [],
            "markers": [],
            "enabled": True,
        }
        clips_children.append(clip)

    video_track = {
        "OTIO_SCHEMA": "Track.1",
        "name": "V1",
        "kind": "Video",
        "children": clips_children,
        "source_range": None,
        "metadata": {},
        "effects": [],
        "markers": [],
        "enabled": True,
    }

    stack = {
        "OTIO_SCHEMA": "Stack.1",
        "name": "tracks",
        "children": [video_track],
        "source_range": None,
        "metadata": {},
        "effects": [],
        "markers": [],
        "enabled": True,
    }

    return {
        "OTIO_SCHEMA": "Timeline.1",
        "name": spec.name or "roughcut",
        "tracks": stack,
        "global_start_time": _rational_time(0.0, fps),
        "metadata": {
            "roughcut": {
                "fps": fps,
                "width": spec.width,
                "height": spec.height,
                "aroll_count": len(spec.aroll),
                "broll_count_skipped": len(spec.broll),
                "note": (
                    "v0.11.0 OTIO export is V1-only. B-roll inserts are "
                    "in the SequenceSpec but not present in this OTIO. "
                    "For multi-track exchange use the .fcpxml sibling."
                ) if spec.broll else "",
            },
        },
    }


def _time_range(start_sec: float, duration_sec: float, fps: float) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": _rational_time(start_sec, fps),
        "duration": _rational_time(duration_sec, fps),
    }


def _rational_time(seconds: float, fps: float) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "RationalTime.1",
        "rate": fps,
        "value": round(float(seconds) * fps, 6),
    }


def _file_url(path: str) -> str:
    """Convert an absolute POSIX path to a file:// URL.

    OTIO consumers expect a URL-form target_url; a bare path is technically
    valid but many readers barf on it.
    """
    p = Path(path)
    if p.is_absolute():
        return f"file://{p.as_posix()}"
    return path
