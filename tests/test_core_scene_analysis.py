"""Tests for `roughcut_core.scene_analysis`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roughcut_core import scene_analysis
from tests.conftest import requires_ffmpeg


def _make_clip(path: Path, duration: int = 2) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=24",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _minimal_analysis(video_path: Path) -> scene_analysis.SceneAnalysis:
    return scene_analysis.SceneAnalysis(
        video_path=str(video_path),
        video_hash="deadbeef",
        duration_sec=2.0,
        one_line="testsrc pattern, static camera",
        shot_count=1,
        shots=[scene_analysis.Shot(
            shot_index=0, start_sec=0.0, end_sec=2.0,
            type="wide", subject="testsrc pattern",
            camera="static", quality="clean",
            notable_events=[],
        )],
        usability_verdict="test pattern only, not usable",
        tags=["test", "pattern"],
    )


def test_shot_defaults() -> None:
    s = scene_analysis.Shot(shot_index=0, start_sec=0.0, end_sec=1.0, type="wide")
    assert s.camera == "static"
    assert s.quality == "clean"
    assert s.notable_events == []


def test_write_read_roundtrip(tmp_path: Path) -> None:
    video = tmp_path / "test.mp4"
    video.write_bytes(b"stub")
    cache = tmp_path / "cache"
    analysis = _minimal_analysis(video)

    out = scene_analysis.write_scene_analysis(video, cache, analysis)
    assert out.is_file()
    assert out.parent.name == "scene-analyses"

    loaded = scene_analysis.read_scene_analysis(video, cache)
    assert loaded is not None
    assert loaded.one_line == analysis.one_line
    assert loaded.shot_count == 1
    assert loaded.shots[0].subject == "testsrc pattern"
    assert loaded.created_at > 0.0


def test_read_returns_none_for_missing(tmp_path: Path) -> None:
    video = tmp_path / "missing.mp4"
    video.write_bytes(b"stub")
    assert scene_analysis.read_scene_analysis(video, tmp_path / "cache") is None


def test_list_scene_analyses_scoping(tmp_path: Path) -> None:
    inside = tmp_path / "shoot"; inside.mkdir()
    outside = tmp_path / "other"; outside.mkdir()
    v_in = inside / "a.mp4"; v_in.write_bytes(b"stub")
    v_out = outside / "b.mp4"; v_out.write_bytes(b"stub")
    cache = tmp_path / "cache"

    scene_analysis.write_scene_analysis(v_in, cache, _minimal_analysis(v_in))
    scene_analysis.write_scene_analysis(v_out, cache, _minimal_analysis(v_out))

    all_of_them = scene_analysis.list_scene_analyses(cache)
    assert len(all_of_them) == 2

    scoped = scene_analysis.list_scene_analyses(cache, folder_path=inside)
    assert len(scoped) == 1
    assert str(v_in.resolve()) == scoped[0].video_path


def test_search_scenes_matches_shot_subject(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"; video.write_bytes(b"stub")
    cache = tmp_path / "cache"
    a = _minimal_analysis(video)
    a.shots[0].subject = "worker at press machine"
    a.shots[0].notable_events = ["camera bump at 8.4s"]
    scene_analysis.write_scene_analysis(video, cache, a)

    hits = scene_analysis.search_scene_analyses("worker", cache)
    assert hits["result_count"] == 1
    assert "worker" in hits["results"][0]["matched_snippets"][0].lower()

    bumps = scene_analysis.search_scene_analyses("bump", cache)
    assert bumps["result_count"] == 1


def test_search_scenes_empty_query(tmp_path: Path) -> None:
    hits = scene_analysis.search_scene_analyses("", tmp_path / "cache")
    assert hits["result_count"] == 0


def test_search_scenes_returns_zero_when_no_match(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"; video.write_bytes(b"stub")
    cache = tmp_path / "cache"
    scene_analysis.write_scene_analysis(video, cache, _minimal_analysis(video))
    hits = scene_analysis.search_scene_analyses("kayak", cache)
    assert hits["result_count"] == 0


@requires_ffmpeg
def test_build_bundle_returns_expected_keys(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    _make_clip(video, duration=2)
    bundle = scene_analysis.build_scene_bundle(
        video, tmp_path / "cache", num_frames=9, sample_hz=2.0,
    )
    assert Path(bundle["contact_sheet_path"]).is_file()
    assert bundle["duration_sec"] > 0
    assert bundle["num_frames"] == 9
    assert "motion" in bundle and "spans" in bundle["motion"]
    assert "shots_from_cuts" in bundle
    assert bundle["prior_analysis"] is None
    assert "schema_template" in bundle
    assert "shots" in bundle["schema_template"]


@requires_ffmpeg
def test_build_bundle_surfaces_prior_analysis(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    _make_clip(video, duration=2)
    cache = tmp_path / "cache"
    scene_analysis.write_scene_analysis(video, cache, _minimal_analysis(video))
    bundle = scene_analysis.build_scene_bundle(
        video, cache, num_frames=9, sample_hz=2.0,
    )
    assert bundle["prior_analysis"] is not None
    assert bundle["prior_analysis"]["one_line"] == "testsrc pattern, static camera"
