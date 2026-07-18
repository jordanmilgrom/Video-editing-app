"""Tests for `roughcut_core.otio_export.write_otio`.

We don't require the opentimelineio wheel to be installed for these
tests — the emitter is a plain JSON writer and we validate structural
shape here. If you have opentimelineio locally, `otiotool cat` will
open the output.
"""

from __future__ import annotations

import json
from pathlib import Path

from roughcut_core import otio_export
from roughcut_core.models import ARollSegment, SequenceSpec


def _fake_spec(tmp_path: Path) -> SequenceSpec:
    a = tmp_path / "a.mov"
    b = tmp_path / "b.mov"
    a.write_bytes(b"stub")
    b.write_bytes(b"stub")
    return SequenceSpec(
        name="test-cut",
        fps=24.0,
        aroll=[
            ARollSegment(source_path=a, source_in_sec=1.0, source_out_sec=3.0),
            ARollSegment(source_path=b, source_in_sec=0.5, source_out_sec=2.5),
        ],
    )


def test_write_otio_emits_valid_timeline(tmp_path: Path) -> None:
    spec = _fake_spec(tmp_path)
    out = tmp_path / "cut.otio"
    otio_export.write_otio(spec, out)

    doc = json.loads(out.read_text())
    assert doc["OTIO_SCHEMA"] == "Timeline.1"
    assert doc["name"] == "test-cut"

    stack = doc["tracks"]
    assert stack["OTIO_SCHEMA"] == "Stack.1"
    tracks = stack["children"]
    assert len(tracks) == 1
    v1 = tracks[0]
    assert v1["kind"] == "Video"
    assert v1["name"] == "V1"

    clips = v1["children"]
    assert len(clips) == 2
    for c in clips:
        assert c["OTIO_SCHEMA"] == "Clip.1"
        sr = c["source_range"]
        assert sr["OTIO_SCHEMA"] == "TimeRange.1"
        assert sr["start_time"]["rate"] == 24.0
        assert sr["duration"]["rate"] == 24.0
        assert c["active_media_reference_key"] == "DEFAULT_MEDIA"
        ref = c["media_references"]["DEFAULT_MEDIA"]
        assert ref["target_url"].startswith("file://")


def test_write_otio_second_clip_source_range(tmp_path: Path) -> None:
    spec = _fake_spec(tmp_path)
    out = tmp_path / "cut.otio"
    otio_export.write_otio(spec, out)
    doc = json.loads(out.read_text())
    clip1 = doc["tracks"]["children"][0]["children"][1]
    # source_in=0.5, dur = 2.0, fps = 24 → start value 12, duration value 48
    assert clip1["source_range"]["start_time"]["value"] == 12.0
    assert clip1["source_range"]["duration"]["value"] == 48.0


def test_write_otio_notes_skipped_broll_in_metadata(tmp_path: Path) -> None:
    from roughcut_core.models import BRollInsert
    a = tmp_path / "a.mov"; a.write_bytes(b"stub")
    b = tmp_path / "b.mov"; b.write_bytes(b"stub")
    spec = SequenceSpec(
        aroll=[ARollSegment(source_path=a, source_in_sec=0.0, source_out_sec=1.0)],
        broll=[BRollInsert(
            source_path=b, source_in_sec=0.0, source_out_sec=1.0,
            timeline_offset_sec=0.0,
        )],
    )
    out = tmp_path / "cut.otio"
    otio_export.write_otio(spec, out)
    doc = json.loads(out.read_text())
    assert doc["metadata"]["roughcut"]["broll_count_skipped"] == 1
    assert "V1-only" in doc["metadata"]["roughcut"]["note"]
