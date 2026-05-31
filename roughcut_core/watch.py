"""Multi-modal segment 'watching': frames + transcript + audio in one call.

The agent calls `watch_segment(video_path, start_sec, end_sec)` when it
needs to actually SEE a moment instead of inferring from statistical
detectors. The result bundles three things:

  1. A contact-sheet image of `num_frames` (default 16) evenly-spaced
     frames inside [start_sec, end_sec], with timecodes overlaid. The
     agent vision-reads this to see eye contact, energy, gestures.
  2. The transcript words inside the window (if a transcript was
     supplied) so the agent can correlate what was said with each frame.
  3. Audio energy stats (min/mean/max dBFS, silence %, speech %) so
     the agent can tell loud sections from quiet ones without re-running
     a detector.

Deterministic. No LLM imports. The MCP wrapper returns the JPEG inline
as an Image plus the sidecar JSON as TextContent, exactly the pattern
already used by `extract_frame_grid`.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from roughcut_core import audio as _audio
from roughcut_core import broll, cache_io, transcribe
from roughcut_core.models import Transcript

NUM_FRAMES_DEFAULT = 16
ENVELOPE_FPS = 1000.0 / _audio.FRAME_MS  # 20 frames/sec at 50ms windows


def _segment_timestamps(start_sec: float, end_sec: float, n: int) -> list[float]:
    """n timestamps evenly spaced inside [start_sec, end_sec], inset from edges.

    Edge inset is 5% of the window, capped at 0.2s — smaller than the
    whole-clip variant in `broll._frame_timestamps` because watch windows
    are typically much shorter than full clips.
    """
    if end_sec <= start_sec or n <= 0:
        return []
    duration = end_sec - start_sec
    edge = min(0.2, duration * 0.05)
    a = start_sec + edge
    b = end_sec - edge
    if n == 1:
        return [round((a + b) / 2, 3)]
    step = (b - a) / (n - 1)
    return [round(a + i * step, 3) for i in range(n)]


def _build_segment_sheet(
    video_path: Path,
    out_path: Path,
    *,
    timestamps: list[float],
    tile_size: int,
    jpeg_quality: int,
    overlay_timecodes: bool,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = len(timestamps)
    cols = int(math.ceil(math.sqrt(max(1, n))))
    rows = int(math.ceil(n / cols))
    cell_w = max(64, int(tile_size))
    cell_h = max(36, cell_w * 9 // 16)
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    font = broll._load_font() if overlay_timecodes else None
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, t in enumerate(timestamps):
            frame_path = Path(tmpdir) / f"f{i:02d}.jpg"
            broll.extract_frame(video_path, t, frame_path)
            img = Image.open(frame_path).convert("RGB").resize((cell_w, cell_h))
            col, row = i % cols, i // cols
            sheet.paste(img, (col * cell_w, row * cell_h))
            if overlay_timecodes and font is not None:
                broll._label_cell(
                    draw, font, f"{t:.2f}s",
                    col * cell_w + 8, row * cell_h + 6,
                )
    sheet.save(
        out_path, format="JPEG",
        quality=max(1, min(95, int(jpeg_quality))), optimize=True,
    )


def _slice_transcript_window(
    transcript_path: Path, start_sec: float, end_sec: float,
) -> dict[str, Any]:
    raw = transcript_path.read_text()
    transcript = Transcript.model_validate_json(raw)
    in_segments: list[dict] = []
    in_words: list[dict] = []
    for seg in transcript.segments:
        if seg.end < start_sec or seg.start > end_sec:
            continue
        in_segments.append({
            "start_sec": round(seg.start, 3),
            "end_sec": round(seg.end, 3),
            "text": seg.text.strip(),
        })
        for w in seg.words:
            if w.end < start_sec or w.start > end_sec:
                continue
            in_words.append({
                "text": w.text,
                "start_sec": round(w.start, 3),
                "end_sec": round(w.end, 3),
            })
    text = " ".join(s["text"] for s in in_segments).strip()
    return {
        "segment_count": len(in_segments),
        "word_count": len(in_words),
        "text": text,
        "segments": in_segments,
        "words": in_words,
    }


def _audio_window_stats(
    video_path: Path, start_sec: float, end_sec: float,
    *, silence_db: float = -40.0, speech_db: float = -30.0,
) -> dict[str, Any]:
    envelope = _audio.decode_audio_envelope(video_path)
    i0 = max(0, int(start_sec * ENVELOPE_FPS))
    i1 = min(len(envelope), int(end_sec * ENVELOPE_FPS))
    win = envelope[i0:i1]
    if len(win) == 0:
        return {
            "min_db": None, "mean_db": None, "max_db": None,
            "silence_pct": 0.0, "speech_pct": 0.0,
            "silence_threshold_db": silence_db,
            "speech_threshold_db": speech_db,
        }
    return {
        "min_db": round(float(np.min(win)), 2),
        "mean_db": round(float(np.mean(win)), 2),
        "max_db": round(float(np.max(win)), 2),
        "silence_pct": round(float(np.mean(win < silence_db)) * 100.0, 1),
        "speech_pct": round(float(np.mean(win > speech_db)) * 100.0, 1),
        "silence_threshold_db": silence_db,
        "speech_threshold_db": speech_db,
    }


def watch_segment(
    video_path: Path,
    start_sec: float,
    end_sec: float,
    cache_dir: Path,
    *,
    transcript_path: Path | None = None,
    num_frames: int = NUM_FRAMES_DEFAULT,
    tile_size: int = broll.DEFAULT_TILE_SIZE,
    jpeg_quality: int = broll.DEFAULT_JPEG_QUALITY,
    overlay_timecodes: bool = True,
) -> dict[str, Any]:
    """Bundle frames + transcript + audio stats for one time window.

    `transcript_path` is optional — without it, the result still includes
    the contact sheet and audio stats; the agent just doesn't get word
    correlation. Audio stats are best-effort: if ffmpeg can't decode the
    waveform we drop the audio section rather than failing the whole call.

    The contact sheet is cached under `cache_dir/watch-sheets/<key>.jpg`
    keyed on (source identity, window, grid params), so repeat calls with
    identical args are free.
    """
    if end_sec <= start_sec:
        raise ValueError(
            f"end_sec ({end_sec}) must be > start_sec ({start_sec})"
        )
    if num_frames < 1:
        raise ValueError(f"num_frames must be >= 1, got {num_frames}")

    timestamps = _segment_timestamps(start_sec, end_sec, num_frames)

    sheet_dir = cache_dir / "watch-sheets"
    key = cache_io.stable_key(
        "watch", transcribe.cache_key(video_path),
        round(start_sec, 3), round(end_sec, 3),
        num_frames, tile_size, jpeg_quality, int(overlay_timecodes),
    )
    sheet_path = sheet_dir / f"{key}.jpg"
    if not sheet_path.exists():
        _build_segment_sheet(
            video_path, sheet_path,
            timestamps=timestamps,
            tile_size=tile_size, jpeg_quality=jpeg_quality,
            overlay_timecodes=overlay_timecodes,
        )

    result: dict[str, Any] = {
        "image_path": str(sheet_path),
        "video_path": str(video_path),
        "start_sec": round(start_sec, 3),
        "end_sec": round(end_sec, 3),
        "duration_sec": round(end_sec - start_sec, 3),
        "num_frames": num_frames,
        "frame_timestamps_sec": timestamps,
    }

    if transcript_path is not None:
        result["transcript"] = _slice_transcript_window(
            transcript_path, start_sec, end_sec,
        )

    try:
        result["audio_stats"] = _audio_window_stats(
            video_path, start_sec, end_sec,
        )
    except Exception:  # noqa: BLE001
        # Audio decode is best-effort; frames + transcript are the primary
        # signals. Don't fail the call if ffmpeg-audio is unavailable on a
        # weird container.
        result["audio_stats"] = None

    return result
