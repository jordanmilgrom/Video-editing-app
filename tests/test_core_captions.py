"""Tests for the b-roll caption cache (write / read / search)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roughcut_core import captions


def _video(tmp_path: Path, name: str = "clip.mp4") -> Path:
    p = tmp_path / name
    p.write_bytes(b"fake video bytes")
    return p


def test_write_and_read_round_trip(tmp_path: Path) -> None:
    video = _video(tmp_path)
    cache_dir = tmp_path / "cache"
    rec = captions.write_caption(
        video, cache_dir,
        description="Kayaks on a calm lake at sunset.",
        tags=["water", "outdoor", "sunset"],
        mood="calm",
    )
    # Persisted on disk under cache/captions/<key>.json.
    cap_dir = cache_dir / "captions"
    assert cap_dir.is_dir()
    files = list(cap_dir.glob("*.json"))
    assert len(files) == 1
    on_disk = json.loads(files[0].read_text())
    assert on_disk["description"] == "Kayaks on a calm lake at sunset."
    assert on_disk["tags"] == ["water", "outdoor", "sunset"]
    assert on_disk["mood"] == "calm"

    # read_caption returns the same record.
    loaded = captions.read_caption(video, cache_dir)
    assert loaded is not None
    assert loaded["description"] == rec["description"]
    assert loaded["tags"] == rec["tags"]


def test_read_returns_none_when_uncached(tmp_path: Path) -> None:
    video = _video(tmp_path)
    cache_dir = tmp_path / "cache"
    # Never wrote anything; read should return None cleanly.
    assert captions.read_caption(video, cache_dir) is None


def test_search_by_description_substring(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    v1 = _video(tmp_path, "kayak.mp4")
    v2 = _video(tmp_path, "drone.mp4")
    captions.write_caption(v1, cache_dir, "Kayaks on a calm lake.")
    captions.write_caption(v2, cache_dir, "Aerial drone shot of mountains.")

    hits = captions.search_captions("kayak", cache_dir)
    assert hits["result_count"] == 1
    assert "kayak" in hits["results"][0]["description"].lower()


def test_search_by_tag(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    v = _video(tmp_path)
    captions.write_caption(
        v, cache_dir,
        description="A product on a desk.",
        tags=["product", "studio", "static"],
    )
    hits = captions.search_captions("static", cache_dir)
    assert hits["result_count"] == 1


def test_search_by_mood(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    v = _video(tmp_path)
    captions.write_caption(v, cache_dir, "Crowd cheering at a concert.",
                           mood="energetic")
    hits = captions.search_captions("energetic", cache_dir)
    assert hits["result_count"] == 1


def test_search_returns_empty_when_no_match(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    v = _video(tmp_path)
    captions.write_caption(v, cache_dir, "Kayaks on a lake.")
    hits = captions.search_captions("mountain", cache_dir)
    assert hits["result_count"] == 0
    assert hits["results"] == []


def test_search_empty_query_returns_no_results(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    v = _video(tmp_path)
    captions.write_caption(v, cache_dir, "Some content here.")
    hits = captions.search_captions("", cache_dir)
    assert hits["result_count"] == 0


def test_search_scoped_to_folder(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    folder_a = tmp_path / "A"; folder_a.mkdir()
    folder_b = tmp_path / "B"; folder_b.mkdir()
    v_a = folder_a / "a.mp4"; v_a.write_bytes(b"x")
    v_b = folder_b / "b.mp4"; v_b.write_bytes(b"x")
    captions.write_caption(v_a, cache_dir, "lake content")
    captions.write_caption(v_b, cache_dir, "lake content")

    # Both match the query, but folder filter should leave only one.
    hits = captions.search_captions("lake", cache_dir, folder_path=folder_a)
    assert hits["result_count"] == 1
    assert hits["results"][0]["video_path"].endswith("a.mp4")


def test_overwrite_replaces_previous(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    v = _video(tmp_path)
    captions.write_caption(v, cache_dir, "First take.")
    captions.write_caption(v, cache_dir, "Better take with more detail.")
    rec = captions.read_caption(v, cache_dir)
    assert rec["description"] == "Better take with more detail."
    # Still only one file on disk (overwrite, not append).
    files = list((cache_dir / "captions").glob("*.json"))
    assert len(files) == 1


def test_write_rejects_missing_file(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    missing = tmp_path / "does_not_exist.mp4"
    with pytest.raises(FileNotFoundError):
        captions.write_caption(missing, cache_dir, "anything")


def test_list_captions_returns_everything(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    for i in range(3):
        v = tmp_path / f"clip{i}.mp4"
        v.write_bytes(b"x")
        captions.write_caption(v, cache_dir, f"clip {i}")
    listed = captions.list_captions(cache_dir)
    assert listed["caption_count"] == 3
    assert {r["description"] for r in listed["captions"]} == {
        "clip 0", "clip 1", "clip 2",
    }
