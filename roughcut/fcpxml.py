"""FCPXML v1.10 emission.

A-roll on V1 (spine), b-roll on V2 (connected, lane 1). Frame rate snapped
to A-roll source. Absolute file paths so Premiere/Resolve relink cleanly.
"""

from __future__ import annotations

from pathlib import Path

from roughcut.models import Sequence


def write_fcpxml(sequence: Sequence, output_path: Path) -> None:
    """Serialize a Sequence to an FCPXML file on disk."""
    raise NotImplementedError("fcpxml.write_fcpxml lands in the fcpxml pass")
