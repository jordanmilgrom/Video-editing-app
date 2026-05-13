"""Semantic A-roll → b-roll matcher.

Chunks the chosen A-roll into sentences, asks Claude for 0-3 inserts per
sentence from the b-roll library, returns BrollMatch records.
"""

from __future__ import annotations

from roughcut.models import BrollMatch, Clip, Take


def match(takes: list[Take], clips: dict[str, Clip]) -> list[BrollMatch]:
    """Suggest b-roll inserts anchored to the assembled A-roll."""
    raise NotImplementedError("match.match lands in the match pass")
