"""Multicam FCPXML emission tests."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from roughcut_core import fcpxml
from roughcut_core.models import AngleSelection


def test_write_multicam_fcpxml_emits_one_clip_per_angle(tmp_path: Path) -> None:
    # Touch the source files so as_uri() works.
    a = tmp_path / "cam_a.mp4"
    b = tmp_path / "cam_b.mp4"
    a.touch(); b.touch()
    selections = [
        AngleSelection(timeline_in_sec=0.0, timeline_out_sec=5.0,
                       clip_path=a, clip_in_sec=0.0, clip_out_sec=5.0, reason="primary"),
        AngleSelection(timeline_in_sec=5.0, timeline_out_sec=7.0,
                       clip_path=b, clip_in_sec=5.0, clip_out_sec=7.0, reason="reaction"),
        AngleSelection(timeline_in_sec=7.0, timeline_out_sec=12.0,
                       clip_path=a, clip_in_sec=7.0, clip_out_sec=12.0, reason="primary"),
    ]
    out = tmp_path / "out.fcpxml"
    fcpxml.write_multicam_fcpxml(selections, out)
    assert out.exists()
    root = ET.fromstring(out.read_text())
    asset_clips = root.findall(".//spine/asset-clip")
    assert len(asset_clips) == 3
    # Two unique sources should produce two assets.
    assets = root.findall(".//resources/asset")
    assert len(assets) == 2


def test_write_multicam_fcpxml_rejects_empty_list(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        fcpxml.write_multicam_fcpxml([], tmp_path / "out.fcpxml")
