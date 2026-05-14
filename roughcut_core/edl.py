"""CMX 3600 EDL emitter — V1-only universal fallback.

When FCPXML and FCP 7 XML both fail, an EDL still loads in every
editor on Earth. Trade-off: EDL doesn't model layers, so only V1
(A-roll) is emitted; B-roll inserts get noted in trailing comments so
the editor can recreate them by hand.

Format (CMX 3600, non-drop frame):

    TITLE: <name>
    FCM: NON-DROP FRAME

    001  AX       V     C        <src_in> <src_out> <rec_in> <rec_out>
    * FROM CLIP NAME: <basename>
    * SOURCE FILE: <abs path>

Timecode is HH:MM:SS:FF at the sequence's integer timebase (24, 25,
30...). Record TC starts at 01:00:00:00 — the standard one-hour pre-roll
convention.
"""

from __future__ import annotations

from pathlib import Path

from roughcut_core.models import SequenceSpec

PROGRAM_START_FRAMES_OFFSET_HHMMSSFF = (1, 0, 0, 0)  # 01:00:00:00


def write_edl(spec: SequenceSpec, output_path: Path) -> None:
    fps = spec.fps
    lines: list[str] = [
        f"TITLE: {spec.name}",
        "FCM: NON-DROP FRAME",
        "",
    ]
    rec_cursor_sec = _tuple_to_seconds(PROGRAM_START_FRAMES_OFFSET_HHMMSSFF, fps)
    edit_no = 0
    for seg in spec.aroll:
        edit_no += 1
        dur = seg.out_sec - seg.in_sec
        src_in_tc = _seconds_to_tc(seg.in_sec, fps)
        src_out_tc = _seconds_to_tc(seg.out_sec, fps)
        rec_in_tc = _seconds_to_tc(rec_cursor_sec, fps)
        rec_out_tc = _seconds_to_tc(rec_cursor_sec + dur, fps)
        lines.append(
            f"{edit_no:03d}  AX       V     C        "
            f"{src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}"
        )
        lines.append(f"* FROM CLIP NAME: {Path(seg.source_path).name}")
        lines.append(f"* SOURCE FILE: {Path(seg.source_path).resolve()}")
        lines.append("")
        rec_cursor_sec += dur

    if spec.broll:
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


def _seconds_to_tc(seconds: float, fps: float) -> str:
    """Whole-frame HH:MM:SS:FF at the integer timebase (NDF convention).

    Using the integer timebase (24/25/30) as the divisor means 1 hour
    of timebase-time = exactly 01:00:00:00 even at 23.976, which is the
    CMX 3600 non-drop-frame standard.
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
    h, m, s, f = hhmmssff
    fps_int = int(round(fps))
    return h * 3600 + m * 60 + s + f / fps_int
