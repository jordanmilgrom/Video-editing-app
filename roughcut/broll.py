"""B-roll analysis.

For each clip: extract 16 evenly-spaced frames, tile into a 4x4 contact sheet
with source-timecode overlays, send the sheet to Claude vision, persist a
structured Clip to .roughcut-cache/broll/<hash>.json.
"""

from __future__ import annotations

from pathlib import Path

from roughcut.models import Clip


def analyze_clip(clip_path: Path, cache_dir: Path) -> Clip:
    """Produce structured metadata for a single b-roll file."""
    raise NotImplementedError("broll.analyze_clip lands in the broll pass")


def analyze_folder(broll_dir: Path, cache_dir: Path) -> dict[str, Clip]:
    """Analyze every supported clip in a directory; returns {hash: Clip}."""
    raise NotImplementedError("broll.analyze_folder lands in the broll pass")
