"""Clip inventory via ffprobe.

Deterministic. Reads codec / duration / frame rate / resolution / size for
every video file in a folder. The agent uses this to plan its rough-cut
strategy before touching any heavier capability.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from roughcut_core.models import ClipMeta

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mxf"}


def probe_clip(video_path: Path) -> ClipMeta:
    """Run ffprobe on one video file and return a ClipMeta."""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found on PATH")
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(video_path)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None,
    )
    if video_stream is None:
        raise ValueError(f"No video stream in {video_path}")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    fmt = data.get("format", {})
    return ClipMeta(
        path=video_path.resolve(),
        duration_sec=float(fmt.get("duration", video_stream.get("duration", 0.0)) or 0.0),
        fps=_parse_rational(video_stream.get("r_frame_rate", "0/1")),
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        codec=str(video_stream.get("codec_name", "unknown")),
        size_bytes=int(fmt.get("size", 0)),
        has_audio=has_audio,
    )


def list_clips(folder: Path, recursive: bool = True) -> list[ClipMeta]:
    """Inventory every video file in `folder`."""
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(folder)
    pattern = "**/*" if recursive else "*"
    files = sorted(
        p for p in folder.glob(pattern)
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )
    return [probe_clip(f) for f in files]


def _parse_rational(value: str) -> float:
    """Parse an ffprobe rational like '24000/1001' into a float fps."""
    if "/" not in value:
        try:
            return float(value)
        except ValueError:
            return 0.0
    num, den = value.split("/", 1)
    try:
        n = float(num)
        d = float(den)
    except ValueError:
        return 0.0
    return n / d if d != 0 else 0.0
