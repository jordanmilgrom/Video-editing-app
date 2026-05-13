"""B-roll analysis.

For each clip:
1. Probe duration.
2. Extract 16 frames at evenly-spaced timestamps via ffmpeg (fast seek).
3. Tile into a 4x4 contact sheet (Pillow) with the source-timecode of
   each cell overlaid in the corner.
4. Send the sheet to Claude vision; parse structured metadata.
5. Persist Clip JSON to `.roughcut-cache/broll/<hash>.json`.

mlx-whisper is not needed here; everything runs locally with ffmpeg + PIL.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from roughcut import claude
from roughcut.models import Clip
from roughcut.transcribe import _atomic_write_json, _probe_duration, cache_key

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mxf"}
GRID_COLS = 4
GRID_ROWS = 4
NUM_FRAMES = GRID_COLS * GRID_ROWS
CELL_W = 480
CELL_H = 270


def _load_font(size: int = 22) -> ImageFont.ImageFont:
    for path in ("DejaVuSans-Bold.ttf", "Arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _frame_timestamps(duration: float, n: int = NUM_FRAMES) -> list[float]:
    """n timestamps evenly spaced inside the clip, inset from both edges.

    Edge inset avoids two failure modes: seeking past the last decodable
    frame on short clips, and grabbing encoder-delayed black frames at
    t=0. Inset is 5% of duration, capped at 0.5s.
    """
    if duration <= 0 or n <= 0:
        return []
    edge = min(0.5, duration * 0.05)
    start = edge
    end = max(start, duration - edge)
    if n == 1:
        return [round((start + end) / 2, 3)]
    step = (end - start) / (n - 1)
    return [round(start + i * step, 3) for i in range(n)]


def _extract_frame(media_path: Path, timestamp: float, out_path: Path) -> None:
    """Hybrid seek: fast to ~1s before target, then accurate the rest.

    Fast-only seeking misses the tail of short clips (lands on the prior
    keyframe). Accurate-only seeking is slow on long clips. This pattern —
    -ss before -i to get close, -ss after -i to land exactly — is reliable
    on both.
    """
    pre = max(0.0, timestamp - 1.0)
    rest = timestamp - pre
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{pre:.3f}", "-i", str(media_path),
        "-ss", f"{rest:.3f}",
        "-frames:v", "1", "-an",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def build_contact_sheet(media_path: Path, out_path: Path, duration: float) -> list[float]:
    """Write the contact sheet for a clip; return the timestamps used."""
    timestamps = _frame_timestamps(duration)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGB", (CELL_W * GRID_COLS, CELL_H * GRID_ROWS), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    font = _load_font()
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, t in enumerate(timestamps):
            frame_path = Path(tmpdir) / f"f{i:02d}.jpg"
            _extract_frame(media_path, t, frame_path)
            img = Image.open(frame_path).convert("RGB").resize((CELL_W, CELL_H))
            col, row = i % GRID_COLS, i // GRID_COLS
            sheet.paste(img, (col * CELL_W, row * CELL_H))
            _label_cell(draw, font, f"{t:.2f}s", col * CELL_W + 8, row * CELL_H + 6)
    sheet.save(out_path, format="PNG")
    return timestamps


def _label_cell(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, text: str, x: int, y: int) -> None:
    """White text with a black 1px outline so it stays legible on any frame."""
    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        draw.text((x + ox, y + oy), text, font=font, fill="black")
    draw.text((x, y), text, font=font, fill="white")


_DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "motion": {"type": "string"},
        "mood": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "suggested_in_sec": {"type": "number"},
        "suggested_out_sec": {"type": "number"},
        "description": {"type": "string"},
    },
    "required": [
        "subject", "motion", "mood", "tags",
        "suggested_in_sec", "suggested_out_sec", "description",
    ],
}


def analyze_clip(clip_path: Path, cache_dir: Path) -> Clip:
    clip_path = Path(clip_path)
    key = cache_key(clip_path)
    cache_file = cache_dir / "broll" / f"{key}.json"
    if cache_file.exists():
        return Clip.model_validate_json(cache_file.read_text(encoding="utf-8"))

    duration = _probe_duration(clip_path)
    sheet_path = cache_dir / "broll-grids" / f"{key}.png"
    build_contact_sheet(clip_path, sheet_path, duration)

    result = claude.call_vision(
        "describe_broll", sheet_path, _DESCRIBE_SCHEMA, tool_name="describe_clip"
    )
    clip = Clip(
        source_path=clip_path,
        source_hash=key,
        duration=duration,
        subject=str(result.get("subject", "")),
        motion=str(result.get("motion", "")),
        mood=str(result.get("mood", "")),
        tags=[str(t) for t in result.get("tags", [])],
        suggested_in_sec=max(0.0, float(result.get("suggested_in_sec", 0.0))),
        suggested_out_sec=min(duration, float(result.get("suggested_out_sec", duration))),
        description=str(result.get("description", "")),
    )
    _atomic_write_json(cache_file, clip.model_dump_json())
    return clip


def analyze_folder(broll_dir: Path, cache_dir: Path) -> dict[str, Clip]:
    files = sorted(
        p for p in Path(broll_dir).iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )
    out: dict[str, Clip] = {}
    for f in files:
        clip = analyze_clip(f, cache_dir)
        out[clip.source_hash] = clip
    return out
