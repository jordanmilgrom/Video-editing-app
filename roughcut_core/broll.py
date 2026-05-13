"""B-roll frame extraction and contact-sheet tiling.

Deterministic. No LLM calls. The agent reads the produced contact sheet
via vision (through the MCP `extract_frame_grid` tool); the structured
description it generates is not the concern of this module.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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


def build_contact_sheet(
    media_path: Path,
    out_path: Path,
    duration: float,
    *,
    overlay_timecodes: bool = True,
    num_frames: int = NUM_FRAMES,
) -> list[float]:
    """Tile evenly-spaced frames into a contact sheet; return timestamps used.

    The output PNG is `GRID_COLS * GRID_ROWS` cells. When
    `overlay_timecodes` is True each cell is labeled with the source
    timestamp (in seconds) of the frame it shows.
    """
    timestamps = _frame_timestamps(duration, n=num_frames)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGB", (CELL_W * GRID_COLS, CELL_H * GRID_ROWS), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    font = _load_font() if overlay_timecodes else None
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, t in enumerate(timestamps):
            frame_path = Path(tmpdir) / f"f{i:02d}.jpg"
            _extract_frame(media_path, t, frame_path)
            img = Image.open(frame_path).convert("RGB").resize((CELL_W, CELL_H))
            col, row = i % GRID_COLS, i // GRID_COLS
            sheet.paste(img, (col * CELL_W, row * CELL_H))
            if overlay_timecodes and font is not None:
                _label_cell(draw, font, f"{t:.2f}s", col * CELL_W + 8, row * CELL_H + 6)
    sheet.save(out_path, format="PNG")
    return timestamps


def _label_cell(
    draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, text: str, x: int, y: int
) -> None:
    """White text with a black 1px outline so it stays legible on any frame."""
    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        draw.text((x + ox, y + oy), text, font=font, fill="black")
    draw.text((x, y), text, font=font, fill="white")
