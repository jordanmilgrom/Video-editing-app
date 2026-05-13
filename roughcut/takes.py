"""Take detection and best-take selection.

Two strategies:
- With script: fuzzy-align transcript against script lines, group repeats.
- No script: silence-gap split + Claude clustering.

For each cluster, Claude picks the cleanest read via a tool-use call.
"""

from __future__ import annotations

from roughcut.models import Take, Transcript


def detect_takes(transcript: Transcript, script_text: str | None = None) -> list[Take]:
    """Return one Take per cluster, with the chosen variant flagged."""
    raise NotImplementedError("takes.detect_takes lands in the takes pass")
