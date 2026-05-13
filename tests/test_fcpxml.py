"""Tests for FCPXML emission. Parses output back with ElementTree."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from roughcut import fcpxml
from roughcut.models import BrollMatch, Clip, Sequence, Take


def _build_sequence(tmp_path: Path) -> Sequence:
    a_path = tmp_path / "interview.mov"
    a_path.write_bytes(b"x")
    b_path = tmp_path / "broll1.mov"
    b_path.write_bytes(b"x")
    take = Take(
        source_path=a_path,
        source_hash="ahash",
        in_sec=2.0,
        out_sec=12.0,  # 10s
        text="hello world this is fine",
        cluster_id="c0",
        chosen=True,
    )
    clip = Clip(
        source_path=b_path,
        source_hash="bhash",
        duration=8.0,
        subject="ocean",
        suggested_in_sec=0.0,
        suggested_out_sec=8.0,
    )
    match_row = BrollMatch(
        sentence_index=0,
        sentence_text="hello world",
        clip_hash="bhash",
        clip_in_sec=1.0,
        clip_out_sec=3.0,  # 2s b-roll
        aroll_offset_sec=4.0,  # halfway through the take
        reason="visual support",
    )
    return Sequence(
        name="test_cut",
        frame_rate=23.976,
        width=1920,
        height=1080,
        takes=[take],
        broll=[match_row],
        clips={"bhash": clip},
    )


def test_fcpxml_writes_valid_structure(tmp_path: Path) -> None:
    seq = _build_sequence(tmp_path)
    out = tmp_path / "out.fcpxml"
    fcpxml.write_fcpxml(seq, out)
    assert out.exists()

    text = out.read_text()
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<!DOCTYPE fcpxml>" in text

    root = ET.fromstring(text[text.index("<fcpxml"):])
    assert root.tag == "fcpxml"
    assert root.attrib["version"] == "1.10"

    fmt = root.find("./resources/format")
    assert fmt is not None
    assert fmt.attrib["frameDuration"] == "1001/24000s"
    assert fmt.attrib["width"] == "1920"
    assert fmt.attrib["height"] == "1080"

    assets = root.findall("./resources/asset")
    assert len(assets) == 2
    for a in assets:
        assert a.attrib["src"].startswith("file://")
        assert "/" in a.attrib["src"]  # absolute path

    spine_clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert len(spine_clips) == 1
    aroll = spine_clips[0]
    assert aroll.attrib["offset"] == "0s"
    # 10s @ 23.976 = 240 frames * 1001 / 24000 = "240240/24000s" reduced
    assert aroll.attrib["duration"].endswith("s")

    nested = aroll.findall("./asset-clip[@lane='1']")
    assert len(nested) == 1
    broll_clip = nested[0]
    assert broll_clip.attrib["lane"] == "1"
    # B-roll offset is local to parent: 4.0 - 0.0 = 4.0s
    assert broll_clip.attrib["offset"].endswith("s")


def test_fcpxml_time_snapping_to_frame() -> None:
    # 23.976fps: 1s ≈ 24 frames → 24024/24000s, reduced = 1001/1000s
    assert fcpxml._t(1.0, 24000, 1001) == "1001/1000s"
    # 0 and negative → "0s"
    assert fcpxml._t(0.0, 24000, 1001) == "0s"
    assert fcpxml._t(-1.0, 24000, 1001) == "0s"
    # frame-aligned 1/24000s should be a single frame
    assert fcpxml._t(1001 / 24000, 24000, 1001) == "1001/24000s"


def test_fcpxml_ignores_unchosen_takes(tmp_path: Path) -> None:
    seq = _build_sequence(tmp_path)
    seq.takes.append(Take(
        source_path=tmp_path / "alt.mov",
        source_hash="alt",
        in_sec=0.0, out_sec=5.0,
        text="rejected take",
        cluster_id="c0",
        chosen=False,
    ))
    (tmp_path / "alt.mov").write_bytes(b"x")
    out = tmp_path / "out.fcpxml"
    fcpxml.write_fcpxml(seq, out)
    root = ET.fromstring(out.read_text()[out.read_text().index("<fcpxml"):])
    # Still only 2 assets — rejected take's source isn't referenced.
    assets = root.findall("./resources/asset")
    assert len(assets) == 2
