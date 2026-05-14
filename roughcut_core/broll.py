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
# Cell dimensions are now derived from `tile_size` (the long edge in
# px). The constants below are kept only for back-compat with older
# code paths that referenced them.
CELL_W = 480
CELL_H = 270

# v0.6.5 P3 #14: shrink defaults so a 9-tile sheet of 16:9 frames lands
# at ~600 KB instead of bumping into the 1 MB tool-result cap. Agents
# that want larger tiles can pass tile_size up to ~512.
DEFAULT_TILE_SIZE = 256
DEFAULT_JPEG_QUALITY = 70


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


def extract_frame(media_path: Path, timestamp: float, out_path: Path) -> None:
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


# Backwards-compat alias used inside this module.
_extract_frame = extract_frame


def build_contact_sheet(
    media_path: Path,
    out_path: Path,
    duration: float,
    *,
    overlay_timecodes: bool = True,
    num_frames: int = NUM_FRAMES,
    tile_size: int = DEFAULT_TILE_SIZE,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> list[float]:
    """Tile evenly-spaced frames into a contact sheet; return timestamps used.

    The output JPEG is a `ceil(sqrt(num_frames)) × ceil(sqrt(num_frames))`
    grid of cells. Each cell is `tile_size` × `tile_size * 9/16` (16:9
    aspect). `tile_size` defaults to 256 so a 9-frame sheet lands at
    ~600 KB after JPEG encoding; bump to 384 or 512 for higher detail.

    When `overlay_timecodes` is True each cell is labeled with the source
    timestamp (in seconds) of the frame it shows.
    """
    timestamps = _frame_timestamps(duration, n=num_frames)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Square-ish grid layout sized to fit the requested num_frames.
    import math
    cols = int(math.ceil(math.sqrt(max(1, num_frames))))
    rows = int(math.ceil(num_frames / cols))
    cell_w = max(64, int(tile_size))
    cell_h = max(36, int(cell_w * 9 // 16))
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    font = _load_font() if overlay_timecodes else None
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, t in enumerate(timestamps):
            frame_path = Path(tmpdir) / f"f{i:02d}.jpg"
            _extract_frame(media_path, t, frame_path)
            img = Image.open(frame_path).convert("RGB").resize((cell_w, cell_h))
            col, row = i % cols, i // cols
            sheet.paste(img, (col * cell_w, row * cell_h))
            if overlay_timecodes and font is not None:
                _label_cell(draw, font, f"{t:.2f}s",
                            col * cell_w + 8, row * cell_h + 6)
    # JPEG (not PNG) and a quality cap, so the encoded sheet stays well
    # under Claude Desktop's 1 MB tool-result cap when sent inline.
    sheet.save(out_path, format="JPEG",
               quality=max(1, min(95, int(jpeg_quality))), optimize=True)
    return timestamps


def _label_cell(
    draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, text: str, x: int, y: int
) -> None:
    """White text with a black 1px outline so it stays legible on any frame."""
    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        draw.text((x + ox, y + oy), text, font=font, fill="black")
    draw.text((x, y), text, font=font, fill="white")
