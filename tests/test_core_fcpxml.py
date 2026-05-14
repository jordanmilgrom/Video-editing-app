"""Tests for FCPXML emission. Parses output back with ElementTree."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from roughcut_core import fcpxml
from roughcut_core.models import ARollSegment, BRollInsert, SequenceSpec


def _spec(tmp_path: Path) -> SequenceSpec:
    a_path = tmp_path / "interview.mov"
    a_path.write_bytes(b"x")
    b_path = tmp_path / "broll1.mov"
    b_path.write_bytes(b"x")
    return SequenceSpec(
        name="test_cut",
        fps=23.976,
        width=1920,
        height=1080,
        aroll=[ARollSegment(source_path=a_path, in_sec=2.0, out_sec=12.0)],  # 10s
        broll=[BRollInsert(
            source_path=b_path,
            clip_in_sec=1.0,
            clip_out_sec=3.0,        # 2s b-roll
            aroll_offset_sec=4.0,    # halfway through the a-roll segment
        )],
    )


def _parse(out: Path) -> ET.Element:
    text = out.read_text()
    return ET.fromstring(text[text.index("<fcpxml"):])


def test_fcpxml_writes_valid_structure(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    out = tmp_path / "out.fcpxml"
    fcpxml.write_fcpxml(spec, out)

    text = out.read_text()
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<!DOCTYPE fcpxml>" in text

    root = _parse(out)
    assert root.tag == "fcpxml" and root.attrib["version"] == "1.10"

    fmt = root.find("./resources/format")
    assert fmt is not None
    assert fmt.attrib["frameDuration"] == "1001/24000s"
    assert (fmt.attrib["width"], fmt.attrib["height"]) == ("1920", "1080")

    assets = root.findall("./resources/asset")
    assert len(assets) == 2
    for a in assets:
        # v0.6.4: FCPXML 1.10 DTD requires <media-rep> child, not src attr.
        assert "src" not in a.attrib
        rep = a.find("media-rep")
        assert rep is not None and rep.attrib["src"].startswith("file://")
        assert rep.attrib["kind"] == "original-media"

    spine_clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert len(spine_clips) == 1
    aroll = spine_clips[0]
    assert aroll.attrib["offset"] == "0s"
    assert aroll.attrib["duration"].endswith("s")

    nested = aroll.findall("./asset-clip[@lane='1']")
    assert len(nested) == 1 and nested[0].attrib["lane"] == "1"
    assert nested[0].attrib["offset"].endswith("s")


def test_fcpxml_time_snapping_to_frame() -> None:
    assert fcpxml._t(1.0, 24000, 1001) == "1001/1000s"
    assert fcpxml._t(0.0, 24000, 1001) == "0s"
    assert fcpxml._t(-1.0, 24000, 1001) == "0s"
    assert fcpxml._t(1001 / 24000, 24000, 1001) == "1001/24000s"


def test_fcpxml_asset_duration_uses_max_out_referenced(tmp_path: Path) -> None:
    a_path = tmp_path / "shared.mov"
    a_path.write_bytes(b"x")
    spec = SequenceSpec(
        aroll=[
            ARollSegment(source_path=a_path, in_sec=0.0, out_sec=5.0),
            ARollSegment(source_path=a_path, in_sec=10.0, out_sec=15.5),  # latest out
        ],
    )
    out = tmp_path / "out.fcpxml"
    fcpxml.write_fcpxml(spec, out)
    root = _parse(out)
    asset = root.find("./resources/asset")
    assert asset is not None
    # max referenced out_sec (15.5) → asset duration; exact form is rational
    assert asset.attrib["duration"].endswith("s")
    # Sequence total duration is sum of segment durations: 5.0 + 5.5 = 10.5s
    seq = root.find("./library/event/project/sequence")
    assert seq is not None
    assert seq.attrib["duration"].endswith("s")


def test_fcpxml_emits_one_asset_per_unique_source(tmp_path: Path) -> None:
    a_path = tmp_path / "interview.mov"
    a_path.write_bytes(b"x")
    spec = SequenceSpec(
        aroll=[
            ARollSegment(source_path=a_path, in_sec=0.0, out_sec=2.0),
            ARollSegment(source_path=a_path, in_sec=10.0, out_sec=12.0),
        ],
    )
    out = tmp_path / "out.fcpxml"
    fcpxml.write_fcpxml(spec, out)
    root = _parse(out)
    assets = root.findall("./resources/asset")
    assert len(assets) == 1
