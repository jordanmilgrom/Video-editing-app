"""CMX 3600 EDL emitter — V1-only universal fallback.

When FCPXML and FCP 7 XML both fail (a finicky NLE, a corrupted
import), an EDL still loads in every editor on Earth. Trade-off: EDL
doesn't model layers, so only V1 (A-roll) is emitted; B-roll inserts
get noted in trailing comments so the editor can recreate them by hand.

Format (CMX 3600 spec, non-drop frame):

    TITLE: <name>
    FCM: NON-DROP FRAME

    001  AX       V     C        <src_in> <src_out> <rec_in> <rec_out>
    * FROM CLIP NAME: <basename>
    * SOURCE FILE: <abs path>

Timecode is HH:MM:SS:FF at the sequence frame rate. Source TCs start at
00:00:00:00 (we don't have real source TC, so we use file-relative).
Record TC starts at 01:00:00:00 — the standard one-hour-pre-roll
convention that prevents wrap-around at the head.

v0.6.5 also writes a sidecar `.relink.csv` next to the EDL: editors
who import the EDL get reel names like `AX` and need a lookup table
to relink to the real source files. The CSV makes that one click.
"""

from __future__ import annotations

import csv
from pathlib import Path

from roughcut_core.models import SequenceSpec

PROGRAM_START_FRAMES_OFFSET_HHMMSSFF = (1, 0, 0, 0)  # 01:00:00:00

# Short, NLE-friendly note explaining how to relink. Surfaced as
# `edl_note` in the generate_fcpxml tool response so the agent can
# parrot it back to the user when they get a "missing media" prompt.
EDL_RELINK_NOTE = (
    "EDLs reference clips by short reel names (e.g. AX), not by file path. "
    "After importing the .edl, point the NLE at the sibling .relink.csv "
    "(or the 'SOURCE FILE:' comment lines inside the EDL itself) to "
    "relink each reel to its source. In Resolve: right-click the EDL → "
    "'Relink Selected Clips'."
)


def write_edl(spec: SequenceSpec, output_path: Path) -> dict:
    """Write the EDL and a sibling `.relink.csv`. Returns a summary dict.

    The CSV columns are `edit_no,reel,clip_name,source_path,...timecodes`.
    Importers can consume it directly; humans can edit it to fix folder
    moves before relinking.
    """
    fps = spec.fps
    lines: list[str] = [
        f"TITLE: {spec.name}",
        "FCM: NON-DROP FRAME",
        "",
    ]
    rec_cursor_sec = _tuple_to_seconds(PROGRAM_START_FRAMES_OFFSET_HHMMSSFF, fps)
    edit_no = 0
    relink_rows: list[dict[str, str]] = []
    for seg in spec.aroll:
        edit_no += 1
        dur = seg.out_sec - seg.in_sec
        src_in_tc = _seconds_to_tc(seg.in_sec, fps)
        src_out_tc = _seconds_to_tc(seg.out_sec, fps)
        rec_in_tc = _seconds_to_tc(rec_cursor_sec, fps)
        rec_out_tc = _seconds_to_tc(rec_cursor_sec + dur, fps)
        reel = "AX"
        lines.append(
            f"{edit_no:03d}  {reel}       V     C        "
            f"{src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}"
        )
        lines.append(f"* FROM CLIP NAME: {Path(seg.source_path).name}")
        lines.append(f"* SOURCE FILE: {Path(seg.source_path).resolve()}")
        lines.append("")
        relink_rows.append({
            "edit_no": f"{edit_no:03d}",
            "reel": reel,
            "clip_name": Path(seg.source_path).name,
            "source_path": str(Path(seg.source_path).resolve()),
            "src_in_tc": src_in_tc,
            "src_out_tc": src_out_tc,
            "rec_in_tc": rec_in_tc,
            "rec_out_tc": rec_out_tc,
        })
        rec_cursor_sec += dur

    if spec.broll:
        # EDL can't represent V2 cleanly; emit a footer comment so the
        # editor knows what's missing instead of silently dropping it.
        lines.append("* B-ROLL INSERTS (not represented on V1 — recreate by hand):")
        for ins in spec.broll:
            tc = _seconds_to_tc(
                _tuple_to_seconds(PROGRAM_START_FRAMES_OFFSET_HHMMSSFF, fps)
                + ins.aroll_offset_sec, fps,
            )
            lines.append(
                f"*   at program {tc}: {Path(ins.source_path).name} "
                f"({_seconds_to_tc(ins.clip_in_sec, fps)} – "
                f"{_seconds_to_tc(ins.clip_out_sec, fps)})"
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    relink_path = output_path.with_suffix(".relink.csv")
    _write_relink_csv(relink_path, relink_rows)

    return {
        "edl_path": str(output_path),
        "relink_csv_path": str(relink_path),
        "edl_note": EDL_RELINK_NOTE,
        "edit_count": edit_no,
    }


def _write_relink_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["edit_no", "reel", "clip_name", "source_path",
                  "src_in_tc", "src_out_tc", "rec_in_tc", "rec_out_tc"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# Timecode helpers
# ---------------------------------------------------------------------------


def _seconds_to_tc(seconds: float, fps: float) -> str:
    """Whole-frame HH:MM:SS:FF at `fps` (NDF). Negative inputs clamp to 0.

    We use the integer timebase (24, 25, 30...) as the frame divisor,
    not the actual `fps` float. That's the CMX 3600 non-drop-frame
    convention: timecode ticks at the timebase while playback runs at
    the slightly-slower real fps (the discrepancy is what DF mode
    compensates for; we don't emit DF). The upshot is that 1 hour of
    timebase-time = exactly 01:00:00:00 even at 23.976.
    """
    if seconds <= 0:
        return "00:00:00:00"
    fps_int = int(round(fps))
    total_frames = int(round(seconds * fps_int))
    frames = total_frames % fps_int
    total_seconds = total_frames // fps_int
    secs = total_seconds % 60
    mins = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{mins:02d}:{secs:02d}:{frames:02d}"


def _tuple_to_seconds(hhmmssff: tuple[int, int, int, int], fps: float) -> float:
    """Convert (H, M, S, F) to seconds at the timebase. Mirror of `_seconds_to_tc`."""
    h, m, s, f = hhmmssff
    fps_int = int(round(fps))
    return h * 3600 + m * 60 + s + f / fps_int
