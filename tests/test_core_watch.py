"""Tests for `roughcut_core.watch.watch_segment`.

watch_segment bundles three modalities (frames + transcript + audio) for
one time window. These tests exercise the deterministic pieces (timestamp
spacing, transcript slicing, cache key stability) without ffmpeg, then
add @requires_ffmpeg tests for the end-to-end contact-sheet build.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from roughcut_core import watch
from tests.conftest import requires_ffmpeg


def _make_tonal_clip(path: Path, duration: float = 4.0) -> None:
    """A synthetic clip with both video (testsrc) and audio (sine 440Hz)."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=24",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-shortest", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-c:a", "aac",
         str(path)],
        check=True,
    )


def _write_transcript(path: Path, *, words: list[tuple[str, float, float]]) -> None:
    """Write a minimal Transcript JSON with one segment that holds the given words."""
    if not words:
        segments = []
        duration = 0.0
    else:
        segments = [{
            "text": " ".join(w[0] for w in words),
            "start": words[0][1],
            "end": words[-1][2],
            "words": [{"text": t, "start": s, "end": e} for t, s, e in words],
        }]
        duration = words[-1][2]
    payload = {
        "source_path": "/abs/whatever.mov",
        "source_hash": "deadbeef",
        "duration": duration,
        "segments": segments,
    }
    path.write_text(json.dumps(payload))


def test_segment_timestamps_evenly_spaced() -> None:
    ts = watch._segment_timestamps(0.0, 10.0, 5)
    assert len(ts) == 5
    # Strictly increasing, all inside [0.0, 10.0] after edge inset.
    assert ts == sorted(ts)
    assert ts[0] > 0.0
    assert ts[-1] < 10.0


def test_segment_timestamps_single_frame_centered() -> None:
    ts = watch._segment_timestamps(2.0, 6.0, 1)
    assert ts == [4.0]


def test_segment_timestamps_empty_when_inverted() -> None:
    assert watch._segment_timestamps(5.0, 2.0, 8) == []
    assert watch._segment_timestamps(5.0, 5.0, 8) == []


def test_segment_timestamps_zero_count() -> None:
    assert watch._segment_timestamps(0.0, 10.0, 0) == []


def test_slice_transcript_window_filters_by_range(tmp_path: Path) -> None:
    tp = tmp_path / "t.json"
    _write_transcript(tp, words=[
        ("hello", 1.0, 1.5),
        ("world", 2.0, 2.5),
        ("foo", 5.0, 5.5),
    ])
    out = watch._slice_transcript_window(tp, 0.0, 3.0)
    assert out["word_count"] == 2
    assert [w["text"] for w in out["words"]] == ["hello", "world"]
    assert "hello world" in out["text"]


def test_slice_transcript_window_empty_when_outside(tmp_path: Path) -> None:
    tp = tmp_path / "t.json"
    _write_transcript(tp, words=[("hello", 1.0, 1.5)])
    out = watch._slice_transcript_window(tp, 10.0, 20.0)
    assert out["word_count"] == 0
    assert out["text"] == ""


def test_watch_segment_rejects_inverted_range(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="end_sec"):
        watch.watch_segment(
            tmp_path / "x.mp4", 5.0, 2.0, tmp_path / "cache",
        )


def test_watch_segment_rejects_zero_frames(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="num_frames"):
        watch.watch_segment(
            tmp_path / "x.mp4", 0.0, 1.0, tmp_path / "cache", num_frames=0,
        )


@requires_ffmpeg
def test_watch_segment_returns_sheet_and_caches(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    _make_tonal_clip(clip, duration=4.0)
    cache = tmp_path / "cache"

    out = watch.watch_segment(
        clip, 0.5, 3.5, cache, num_frames=4, tile_size=128,
    )

    assert out["start_sec"] == 0.5 and out["end_sec"] == 3.5
    assert out["duration_sec"] == 3.0
    assert out["num_frames"] == 4
    assert len(out["frame_timestamps_sec"]) == 4
    sheet = Path(out["image_path"])
    assert sheet.is_file() and sheet.stat().st_size > 0
    # Cache hit on second call (same args, no re-extract).
    mtime_before = sheet.stat().st_mtime
    out2 = watch.watch_segment(
        clip, 0.5, 3.5, cache, num_frames=4, tile_size=128,
    )
    assert Path(out2["image_path"]).stat().st_mtime == mtime_before


@requires_ffmpeg
def test_watch_segment_audio_stats_present(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    _make_tonal_clip(clip, duration=3.0)
    out = watch.watch_segment(
        clip, 0.5, 2.5, tmp_path / "cache", num_frames=4, tile_size=128,
    )
    stats = out["audio_stats"]
    assert stats is not None
    # Sine wave is far from silent; mean_db should be well above -60 dBFS.
    assert stats["mean_db"] is not None and stats["mean_db"] > -60.0


@requires_ffmpeg
def test_watch_segment_with_transcript_includes_words(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    _make_tonal_clip(clip, duration=3.0)
    tp = tmp_path / "t.json"
    _write_transcript(tp, words=[
        ("alpha", 0.6, 1.0),
        ("beta", 1.2, 1.6),
        ("gamma", 4.0, 4.5),  # outside the watch window
    ])
    out = watch.watch_segment(
        clip, 0.5, 2.5, tmp_path / "cache",
        transcript_path=tp, num_frames=4, tile_size=128,
    )
    assert "transcript" in out
    words = [w["text"] for w in out["transcript"]["words"]]
    assert "alpha" in words and "beta" in words and "gamma" not in words


@requires_ffmpeg
def test_watch_segment_without_transcript_omits_words(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    _make_tonal_clip(clip, duration=3.0)
    out = watch.watch_segment(
        clip, 0.5, 2.5, tmp_path / "cache", num_frames=4, tile_size=128,
    )
    assert "transcript" not in out
