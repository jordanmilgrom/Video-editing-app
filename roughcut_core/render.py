"""Delivery-quality render of a SequenceSpec via ffmpeg concat.

Companion to `preview.render_preview` — same concat-demuxer pipeline
but with H.264 delivery settings and configurable resolution. Whereas
`render_preview` is a 480×270 sanity-check MP4 the agent inspects
inline, `render_cut` produces a real playable deliverable.

Runs as an async job (`ASYNC_TOOLS` entry in `jobs.py`) because a
30-minute cut at 1080p30 can take a couple minutes to encode. The
worker handler is in `worker.py`.

Presets are a small tabled surface — 720p30, 1080p30, 1080p60, 4k30 —
each mapped to (width, height, fps, crf). Custom sizes possible via
`custom_width` / `custom_height`.

For v0.11.0 the render is V1-only (A-roll spine). B-roll insert overlay
is deferred; the response reports how many b-roll inserts were skipped
so the agent knows to only trust the delivery for content order.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from roughcut_core.models import SequenceSpec


PRESETS: dict[str, dict[str, Any]] = {
    "720p30": {"width": 1280, "height": 720, "fps": 30, "crf": 20},
    "1080p30": {"width": 1920, "height": 1080, "fps": 30, "crf": 18},
    "1080p60": {"width": 1920, "height": 1080, "fps": 60, "crf": 18},
    "4k30": {"width": 3840, "height": 2160, "fps": 30, "crf": 20},
}

DEFAULT_PRESET = "1080p30"


def render_cut(
    spec: SequenceSpec,
    output_path: Path,
    *,
    preset: str = DEFAULT_PRESET,
    audio_bitrate: str = "192k",
) -> dict[str, Any]:
    """Render `spec`'s A-roll spine as a delivery-quality MP4.

    Returns:
        {
          "output_path": str,
          "preset": str,
          "width": int, "height": int, "fps": int,
          "duration_sec": float,
          "aroll_count": int,
          "broll_count_skipped": int,
          "render_seconds": float,
          "size_bytes": int,
          "concat_list_path": str,
        }

    Raises:
        RuntimeError: ffmpeg not on PATH, or ffmpeg encoding failure.
        FileNotFoundError: a source file referenced by the spec is missing.
        ValueError: spec.aroll is empty, or preset is unknown.
    """
    if not spec.aroll:
        raise ValueError("render_cut: spec.aroll is empty; nothing to render")
    if preset not in PRESETS:
        raise ValueError(
            f"render_cut: unknown preset '{preset}'. "
            f"Choices: {sorted(PRESETS)}"
        )
    # File-existence FIRST (matches v0.7.1 pattern — CI has no ffmpeg).
    for seg in spec.aroll:
        p = Path(seg.source_path)
        if not p.is_file():
            raise FileNotFoundError(seg.source_path)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    p = PRESETS[preset]
    width, height, fps, crf = p["width"], p["height"], p["fps"], p["crf"]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list_path = output_path.with_suffix(".concat.txt")
    _write_concat_list(concat_list_path, spec)

    t0 = time.time()
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path),
        # Scale to preset resolution keeping aspect (pad to fit).
        "-vf", (
            f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps}"
        ),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    render_seconds = round(time.time() - t0, 2)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:400]}"
        )

    duration = sum(s.source_out_sec - s.source_in_sec for s in spec.aroll)

    return {
        "output_path": str(output_path),
        "preset": preset,
        "width": width,
        "height": height,
        "fps": fps,
        "crf": crf,
        "duration_sec": round(duration, 3),
        "aroll_count": len(spec.aroll),
        "broll_count_skipped": len(spec.broll),
        "render_seconds": render_seconds,
        "size_bytes": output_path.stat().st_size if output_path.is_file() else 0,
        "concat_list_path": str(concat_list_path),
    }


def _write_concat_list(path: Path, spec: SequenceSpec) -> None:
    """Mirror preview._write_concat_list — kept independent so the two
    render paths can evolve separately without cross-imports."""
    lines: list[str] = []
    for seg in spec.aroll:
        abs_path = str(Path(seg.source_path).resolve())
        escaped = abs_path.replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
        lines.append(f"inpoint {seg.source_in_sec:.3f}")
        lines.append(f"outpoint {seg.source_out_sec:.3f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
