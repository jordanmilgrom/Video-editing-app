"""Tests for `roughcut_core.index.index_project`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from roughcut_core import captions, index as project_index
from tests.conftest import requires_ffmpeg


def _make_video_only(path: Path, *, duration: int = 1) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=24",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _make_av_clip(path: Path, *, duration: int = 1) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=24",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-shortest", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-c:a", "aac", str(path)],
        check=True,
    )


def _write_transcript_for(video_path: Path, cache_dir: Path,
                          *, segments: list[dict]) -> Path:
    """Manually plant a Transcript JSON at the cache location for `video_path`."""
    import hashlib
    stat = video_path.stat()
    key_str = f"{video_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    key = hashlib.sha256(key_str.encode()).hexdigest()
    out_dir = cache_dir / "transcripts" / "small"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{key}.json"
    payload = {
        "source_path": str(video_path.resolve()),
        "source_hash": key,
        "duration": max(1.0, segments[-1]["end"]) if segments else 1.0,
        "segments": segments,
    }
    out.write_text(json.dumps(payload))
    return out


def test_index_project_rejects_nonexistent_folder(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        project_index.index_project(tmp_path / "missing", tmp_path / "cache")


@requires_ffmpeg
def test_index_project_flags_silent_and_transcribed(tmp_path: Path) -> None:
    folder = tmp_path / "shoot"
    folder.mkdir()
    cache = tmp_path / "cache"

    silent = folder / "broll_silent.mp4"
    _make_video_only(silent, duration=1)

    interview = folder / "interview.mp4"
    _make_av_clip(interview, duration=1)

    _write_transcript_for(
        interview, cache,
        segments=[{
            "text": "Hello world I am talking now.",
            "start": 0.0, "end": 1.0,
            "words": [],
        }],
    )

    result = project_index.index_project(folder, cache)

    assert result["clip_count"] == 2
    assert result["silent_count"] == 1
    assert result["transcribed_count"] == 1

    by_name = {c["filename"]: c for c in result["clips"]}
    assert by_name["broll_silent.mp4"]["has_audio"] is False
    assert by_name["broll_silent.mp4"]["transcript"]["status"] == "silent"
    assert by_name["interview.mp4"]["has_audio"] is True
    assert by_name["interview.mp4"]["transcript"]["status"] == "cached"
    assert (
        "Hello world"
        in by_name["interview.mp4"]["transcript"]["opening_200_chars"]
    )


@requires_ffmpeg
def test_index_project_includes_top_segments(tmp_path: Path) -> None:
    folder = tmp_path / "shoot"
    folder.mkdir()
    cache = tmp_path / "cache"
    clip = folder / "clip.mp4"
    _make_av_clip(clip, duration=1)
    _write_transcript_for(
        clip, cache,
        segments=[
            {"text": "short", "start": 0.0, "end": 0.2, "words": []},
            {"text": "this is a much longer segment with more words",
             "start": 0.2, "end": 0.6, "words": []},
            {"text": "medium length here", "start": 0.6, "end": 1.0, "words": []},
        ],
    )
    result = project_index.index_project(folder, cache, include_top_segments=2)
    tops = result["clips"][0]["transcript"]["top_segments"]
    assert len(tops) == 2
    # Longest first.
    assert "much longer" in tops[0]["text"]


@requires_ffmpeg
def test_index_project_surfaces_caption(tmp_path: Path) -> None:
    folder = tmp_path / "shoot"
    folder.mkdir()
    cache = tmp_path / "cache"
    clip = folder / "vista.mp4"
    _make_video_only(clip, duration=1)
    captions.write_caption(
        clip, cache, "wide shot of mountains at sunrise",
        tags=["nature", "wide"], mood="calm",
    )
    result = project_index.index_project(folder, cache)
    entry = result["clips"][0]
    assert entry["caption"]["description"] == "wide shot of mountains at sunrise"
    assert entry["caption"]["tags"] == ["nature", "wide"]
    assert entry["caption"]["mood"] == "calm"
    assert result["captioned_count"] == 1
