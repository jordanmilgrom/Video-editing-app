"""Tests for the generate_fcpxml MCP tool wrapper."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from roughcut_mcp import tools
from tests.conftest import requires_ffmpeg


def _spec_dict(tmp_path: Path) -> dict:
    a = tmp_path / "interview.mov"
    a.write_bytes(b"x")
    b = tmp_path / "broll.mov"
    b.write_bytes(b"x")
    return {
        "name": "test",
        "fps": 23.976,
        "width": 1920,
        "height": 1080,
        "aroll": [
            {"source_path": str(a), "in_sec": 0.0, "out_sec": 5.0},
            {"source_path": str(a), "in_sec": 10.0, "out_sec": 13.0},
        ],
        "broll": [
            {"source_path": str(b), "clip_in_sec": 0.0, "clip_out_sec": 2.0,
             "aroll_offset_sec": 1.0},
        ],
    }


def test_generate_fcpxml_rejects_relative_output_path(tmp_path: Path) -> None:
    res = tools._generate_fcpxml(_spec_dict(tmp_path), "relative.fcpxml")
    assert res.ok is False
    assert res.error == "relative_path"


def test_generate_fcpxml_rejects_invalid_spec(tmp_path: Path) -> None:
    res = tools._generate_fcpxml({"not": "a sequence spec"}, str(tmp_path / "out.fcpxml"))
    assert res.ok is False
    assert res.error == "invalid_spec"


def test_generate_fcpxml_rejects_empty_aroll(tmp_path: Path) -> None:
    spec = _spec_dict(tmp_path)
    spec["aroll"] = []
    res = tools._generate_fcpxml(spec, str(tmp_path / "out.fcpxml"))
    assert res.ok is False
    assert res.error == "invalid_spec"
    assert "aroll" in (res.message or "").lower()


def test_generate_fcpxml_writes_and_returns_summary(tmp_path: Path) -> None:
    out = tmp_path / "cut.fcpxml"
    res = tools._generate_fcpxml(_spec_dict(tmp_path), str(out))
    assert res.ok is True
    # v0.6.4: output_path is the .fcpxml; the summary also lists the
    # sibling FCP 7 XML and EDL emitted alongside.
    assert res.output_path == str(out)
    s = res.summary or {}
    assert s["aroll_count"] == 2
    assert s["broll_count"] == 1
    assert s["duration_sec"] == 8.0
    assert s["fcpxml_path"] == str(out)
    assert s["fcp7_xml_path"].endswith(".xml")
    assert s["edl_path"].endswith(".edl")
    assert out.exists()
    assert Path(s["fcp7_xml_path"]).exists()
    assert Path(s["edl_path"]).exists()

    # Quick sanity-parse — the XML should round-trip and contain the lane-1 b-roll.
    text = out.read_text()
    root = ET.fromstring(text[text.index("<fcpxml"):])
    nested = root.findall("./library/event/project/sequence/spine/asset-clip/asset-clip[@lane='1']")
    assert len(nested) == 1


@requires_ffmpeg
def test_generate_fcpxml_rejects_source_out_past_file_duration(tmp_path: Path) -> None:
    """v0.7.0 P0: every source is ffprobed; specs that request time past
    the file's actual duration are rejected with a per-segment error
    list — never silently shipped to FCP / Premiere as media-not-found.
    """
    short_clip = tmp_path / "short.mp4"
    # 1.0s of test source.
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=160x90:rate=24",
         "-pix_fmt", "yuv420p", str(short_clip)],
        check=True,
    )
    spec = {
        "name": "stretched-clip-test", "fps": 23.976,
        "width": 1920, "height": 1080,
        # Lie about how long the clip is — request 5s of media from a 1s file.
        "aroll": [{"source_path": str(short_clip),
                   "in_sec": 0.0, "out_sec": 5.0}],
    }
    res = tools._generate_fcpxml(spec, str(tmp_path / "out.fcpxml"))
    assert res.ok is False
    assert res.error == "invalid_spec"
    assert "source_out_sec" in (res.message or "")
    assert (res.data or {}).get("bounds_errors")
    # Output files must NOT have been written.
    assert not (tmp_path / "out.fcpxml").exists()
