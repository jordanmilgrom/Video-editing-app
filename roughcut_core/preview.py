"""Render a low-res MP4 preview of a SequenceSpec so the agent (or human)
can sanity-check a cut before opening Final Cut.

Pipeline:

  1. Write an ffmpeg concat-demuxer file list with one block per A-roll
     segment, each block specifying `file <abs path>`, `inpoint X.X`,
     and `outpoint Y.Y`.
  2. Run ffmpeg with `-f concat -safe 0 -i list.txt -vf scale=...
     -c:v libx264 -preset ultrafast -crf 28 -c:a aac` to produce the
     preview.

For v0.8.0 the preview is V1-only — B-roll inserts are documented in
the response so the agent / user knows what's missing, but rendering
overlays on V2 is deferred. A V1-only preview is enough to QC pacing,
content, and dead air; visual b-roll choice can be QC'd by listing
each insert's source + timeline range.

The output is intentionally tiny (480x270 @ CRF 28, ultrafast) so it
renders in a few seconds even on a long cut and stays well under
Claude Desktop's 1 MB tool-result cap when sent inline.

Cached: hashing the spec's contents and the height/quality means
re-rendering the same cut is a no-op.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from roughcut_core import cache_io
from roughcut_core.models import SequenceSpec

DEFAULT_HEIGHT = 270  # 480x270 at 16:9
DEFAULT_CRF = 28      # higher = smaller. 28 is "preview quality".


def render_preview(
    spec: SequenceSpec,
    output_path: Path,
    height: int = DEFAULT_HEIGHT,
    crf: int = DEFAULT_CRF,
) -> dict:
    """Render `spec`'s A-roll spine to a low-res MP4 at `output_path`.

    Returns:
        {
          "preview_path": str,
          "duration_sec": float,
          "aroll_count": int,
          "broll_count_skipped": int,
          "render_seconds": float,
          "size_bytes": int,
          "concat_list_path": str,
          "skipped_broll_note": str | None,  # if any
        }

    Raises:
        RuntimeError: ffmpeg not on PATH.
        FileNotFoundError: a source file referenced by the spec is missing.
        ValueError: spec.aroll is empty.
    """
    if not spec.aroll:
        raise ValueError("render_preview: spec.aroll is empty; nothing to render")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Verify every source file exists before writing the concat list —
    # ffmpeg's error on a missing file is cryptic.
    for seg in spec.aroll:
        p = Path(seg.source_path)
        if not p.is_file():
            raise FileNotFoundError(seg.source_path)

    # ffmpeg concat demuxer expects: "file '<path>'\ninpoint X\noutpoint Y"
    # per segment. Paths must be quoted to handle spaces; single quotes
    # inside paths need escaping per the demuxer's rules.
    concat_list_path = output_path.with_suffix(".concat.txt")
    _write_concat_list(concat_list_path, spec)

    t0 = time.time()
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path),
        "-vf", f"scale=-2:{height}:flags=fast_bilinear",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", str(crf),
        "-c:a", "aac",
        "-b:a", "96k",
        "-pix_fmt", "yuv420p",
        # Force a single output stream timeline rather than copying source TC.
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
    broll_skipped = len(spec.broll)
    skipped_note = (
        f"{broll_skipped} B-roll insert(s) not visible in this preview — "
        f"V1-only render for v0.8.0. They're in the SequenceSpec and will "
        f"appear in the FCPXML/XML/EDL exports."
    ) if broll_skipped else None

    return {
        "preview_path": str(output_path),
        "duration_sec": round(duration, 3),
        "aroll_count": len(spec.aroll),
        "broll_count_skipped": broll_skipped,
        "render_seconds": render_seconds,
        "size_bytes": output_path.stat().st_size if output_path.is_file() else 0,
        "concat_list_path": str(concat_list_path),
        "skipped_broll_note": skipped_note,
    }


def preview_cache_key(spec: SequenceSpec, height: int, crf: int) -> str:
    """Stable key identifying this (spec, render-settings) pair. Mirrors
    `cache_io.stable_key` so callers can skip re-rendering identical cuts.
    """
    parts = [
        f"h={height}", f"crf={crf}",
        f"aroll={len(spec.aroll)}", f"broll={len(spec.broll)}",
    ]
    for seg in spec.aroll:
        parts.append(
            f"a|{Path(seg.source_path).resolve()}|"
            f"{seg.source_in_sec:.3f}|{seg.source_out_sec:.3f}"
        )
    for ins in spec.broll:
        parts.append(
            f"b|{Path(ins.source_path).resolve()}|"
            f"{ins.source_in_sec:.3f}|{ins.source_out_sec:.3f}|"
            f"{ins.timeline_offset_sec:.3f}"
        )
    return cache_io.stable_key(*parts)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _write_concat_list(path: Path, spec: SequenceSpec) -> None:
    """Emit ffmpeg's concat-demuxer file list for the A-roll spine.

    Format (per segment):
        file '<abs path with single-quotes escaped>'
        inpoint <source_in_sec>
        outpoint <source_out_sec>
    """
    lines: list[str] = []
    for seg in spec.aroll:
        abs_path = str(Path(seg.source_path).resolve())
        # The ffmpeg concat demuxer treats single quotes specially —
        # escape any literal single quote in the path.
        escaped = abs_path.replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
        lines.append(f"inpoint {seg.source_in_sec:.3f}")
        lines.append(f"outpoint {seg.source_out_sec:.3f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
