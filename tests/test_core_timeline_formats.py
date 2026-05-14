"""Tests for the three timeline emitters: FCPXML 1.10, FCP 7 XMEML, EDL.

The v0.6.3 FCPXML output got rejected by Final Cut Pro with "No
declaration for attribute src of element asset" — the asset element
needs a `<media-rep>` child, not a `src` attribute. Plus we now ship
FCP 7 XML and CMX 3600 EDL siblings for Premiere and as a universal
fallback. These tests check well-formedness and that all three files
reference the same source path.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from roughcut_core import edl, fcp7_xml, fcpxml
from roughcut_core.models import ARollSegment, BRollInsert, SequenceSpec


def _spec(tmp_path: Path) -> SequenceSpec:
    """Two A-roll segments from one clip + one B-roll insert from another."""
    src_a = tmp_path / "interview.mp4"
    src_b = tmp_path / "broll.mp4"
    src_a.touch()
    src_b.touch()
    return SequenceSpec(
        name="documentary-roughcut", fps=23.976, width=1920, height=1080,
        aroll=[
            ARollSegment(source_path=src_a, in_sec=0.0, out_sec=5.0),
            ARollSegment(source_path=src_a, in_sec=10.0, out_sec=15.0),
        ],
        broll=[
            BRollInsert(source_path=src_b, clip_in_sec=0.0,
                        clip_out_sec=2.0, aroll_offset_sec=2.0),
        ],
    )


# ----- FCPXML 1.10 (DTD-compliant) ------------------------------------------


def test_fcpxml_uses_media_rep_child_not_src_attr(tmp_path: Path) -> None:
    """v0.6.4 bug fix: asset must NOT have a src attribute; FCP rejects that."""
    out = tmp_path / "cut.fcpxml"
    fcpxml.write_fcpxml(_spec(tmp_path), out)
    root = ET.fromstring(out.read_text())
    assets = root.findall(".//resources/asset")
    assert len(assets) == 2, f"expected 2 unique assets, got {len(assets)}"
    for asset in assets:
        assert "src" not in asset.attrib, (
            f"asset still has bare src attr: {asset.attrib} "
            "— FCP 1.10 DTD rejects this"
        )
        media_reps = asset.findall("media-rep")
        assert len(media_reps) == 1
        rep = media_reps[0]
        assert rep.attrib["kind"] == "original-media"
        assert rep.attrib["src"].startswith("file:///")


def test_fcpxml_xml_well_formed(tmp_path: Path) -> None:
    out = tmp_path / "cut.fcpxml"
    fcpxml.write_fcpxml(_spec(tmp_path), out)
    body = out.read_text()
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<!DOCTYPE fcpxml>" in body
    # ET.fromstring would raise if it isn't well-formed XML.
    ET.fromstring(body)


# ----- FCP 7 XMEML v5 -------------------------------------------------------


def test_fcp7_xml_emits_xmeml_v5(tmp_path: Path) -> None:
    out = tmp_path / "cut.xml"
    fcp7_xml.write_fcp7_xml(_spec(tmp_path), out)
    root = ET.fromstring(out.read_text())
    assert root.tag == "xmeml"
    assert root.attrib.get("version") == "5"
    # Sequence + rate + media + video + at least one track.
    assert root.find(".//sequence") is not None
    rates = root.findall(".//rate/timebase")
    assert any(r.text == "24" for r in rates)  # 23.976 ntsc → 24 timebase
    ntscs = root.findall(".//rate/ntsc")
    assert any(n.text == "TRUE" for n in ntscs)


def test_fcp7_xml_first_file_declares_pathurl(tmp_path: Path) -> None:
    out = tmp_path / "cut.xml"
    fcp7_xml.write_fcp7_xml(_spec(tmp_path), out)
    root = ET.fromstring(out.read_text())
    # First reference to a file should declare pathurl; subsequent refs
    # use the empty <file id="..."/> form.
    pathurls = root.findall(".//pathurl")
    assert len(pathurls) == 2, "one pathurl per unique source file"
    files = root.findall(".//file")
    # 2 unique sources × A-roll(2)+B-roll(1) = 3 clipitems, 3 file refs.
    assert len(files) == 3


def test_fcp7_xml_v2_track_is_broll(tmp_path: Path) -> None:
    out = tmp_path / "cut.xml"
    fcp7_xml.write_fcp7_xml(_spec(tmp_path), out)
    root = ET.fromstring(out.read_text())
    tracks = root.findall(".//video/track")
    assert len(tracks) == 2
    v1_clipitems = tracks[0].findall("clipitem")
    v2_clipitems = tracks[1].findall("clipitem")
    assert len(v1_clipitems) == 2
    assert len(v2_clipitems) == 1
    assert "broll" in v2_clipitems[0].find("name").text


# ----- CMX 3600 EDL ---------------------------------------------------------


def test_edl_emits_two_v1_edits(tmp_path: Path) -> None:
    out = tmp_path / "cut.edl"
    edl.write_edl(_spec(tmp_path), out)
    body = out.read_text()
    assert body.startswith("TITLE: documentary-roughcut")
    assert "FCM: NON-DROP FRAME" in body
    # Two A-roll edits → 001 and 002.
    assert re.search(r"^001\s+AX\s+V\s+C", body, re.MULTILINE)
    assert re.search(r"^002\s+AX\s+V\s+C", body, re.MULTILINE)
    # Source file comment.
    assert "* FROM CLIP NAME: interview.mp4" in body


def test_edl_program_timecode_starts_at_01_hours(tmp_path: Path) -> None:
    """Standard one-hour pre-roll on the record side."""
    out = tmp_path / "cut.edl"
    edl.write_edl(_spec(tmp_path), out)
    body = out.read_text()
    first_edit = re.search(r"^001\s+AX\s+V\s+C\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)",
                           body, re.MULTILINE)
    assert first_edit, body
    src_in, src_out, rec_in, rec_out = first_edit.groups()
    assert src_in == "00:00:00:00"
    assert rec_in == "01:00:00:00"


def test_edl_notes_broll_in_footer(tmp_path: Path) -> None:
    """EDL can't represent V2, but we shouldn't silently drop b-roll."""
    out = tmp_path / "cut.edl"
    edl.write_edl(_spec(tmp_path), out)
    body = out.read_text()
    assert "B-ROLL INSERTS" in body
    assert "broll.mp4" in body


# ----- v0.6.5 P0 #2: special chars in paths must round-trip ----------------


def _spec_with_spaces(tmp_path: Path) -> SequenceSpec:
    """Realistic editorial path: spaces, `#`, parens, brackets — all show up
    in real folders like 'Shoot Day 2 (Tuesday)/clip [hero #1].mov'."""
    folder = tmp_path / "Shoot Day 2 (Tuesday)"
    folder.mkdir()
    src = folder / "clip [hero #1].mov"
    src.touch()
    return SequenceSpec(
        name="path-encoding-test", fps=23.976,
        aroll=[ARollSegment(source_path=src, in_sec=0.0, out_sec=2.0)],
    )


def test_fcpxml_pathurl_encodes_special_chars(tmp_path: Path) -> None:
    out = tmp_path / "cut.fcpxml"
    fcpxml.write_fcpxml(_spec_with_spaces(tmp_path), out)
    root = ET.fromstring(out.read_text())
    rep = root.find(".//asset/media-rep")
    assert rep is not None
    src = rep.attrib["src"]
    # Spaces and `#`/`[`/`]` must be percent-encoded; raw chars break
    # FCP's URL parser silently. v0.6.5 P0 #2.
    assert " " not in src, src
    assert "#" not in src, src
    assert "[" not in src and "]" not in src, src
    assert "%20" in src and "%23" in src


def test_fcp7_pathurl_encodes_special_chars(tmp_path: Path) -> None:
    out = tmp_path / "cut.xml"
    fcp7_xml.write_fcp7_xml(_spec_with_spaces(tmp_path), out)
    root = ET.fromstring(out.read_text())
    pathurl = root.find(".//pathurl")
    assert pathurl is not None and pathurl.text is not None
    text = pathurl.text
    assert " " not in text, text
    assert "#" not in text, text
    assert "%20" in text and "%23" in text
