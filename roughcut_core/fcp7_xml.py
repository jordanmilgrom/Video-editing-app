"""FCP 7 (XMEML v5) emitter — Premiere's most reliable import path.

Adobe Premiere imports FCPXML 1.10 spottily; the legacy `xmeml version="5"`
schema from Final Cut Pro 7 was the industry interchange format for a
decade, so Adobe wrote excellent (and stable) support for it. roughcut
emits a `.xml` next to the `.fcpxml` so editors who hit FCPXML problems
have a second path that works.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from roughcut_core.models import SequenceSpec

_RATE_TABLE: dict[float, tuple[int, str]] = {
    23.976: (24, "TRUE"),
    24.0: (24, "FALSE"),
    25.0: (25, "FALSE"),
    29.97: (30, "TRUE"),
    30.0: (30, "FALSE"),
    50.0: (50, "FALSE"),
    59.94: (60, "TRUE"),
    60.0: (60, "FALSE"),
}


def write_fcp7_xml(spec: SequenceSpec, output_path: Path) -> None:
    """Emit FCP 7 (XMEML v5) for `spec`. V1 = A-roll, V2 = B-roll inserts."""
    timebase, ntsc = _rate(spec.fps)
    xmeml = ET.Element("xmeml", {"version": "5"})
    seq = ET.SubElement(xmeml, "sequence", {"id": "sequence-1"})
    ET.SubElement(seq, "name").text = spec.name

    total = sum(s.out_sec - s.in_sec for s in spec.aroll)
    ET.SubElement(seq, "duration").text = str(_frames(total, spec.fps))
    _rate_block(seq, timebase, ntsc)
    ET.SubElement(seq, "in").text = "-1"
    ET.SubElement(seq, "out").text = "-1"
    _timecode_block(seq, timebase, ntsc)

    media = ET.SubElement(seq, "media")
    video = ET.SubElement(media, "video")
    _format_block(video, spec)

    paths: list[Path] = []
    seen: set[Path] = set()
    for s in spec.aroll:
        if s.source_path not in seen:
            seen.add(s.source_path); paths.append(s.source_path)
    for ins in spec.broll:
        if ins.source_path not in seen:
            seen.add(ins.source_path); paths.append(ins.source_path)
    file_ids = {p: f"file-{i}" for i, p in enumerate(paths, start=1)}

    v1 = ET.SubElement(video, "track")
    declared: set[Path] = set()
    cursor = 0
    for clip_idx, seg in enumerate(spec.aroll, start=1):
        in_f = _frames(seg.in_sec, spec.fps)
        out_f = _frames(seg.out_sec, spec.fps)
        dur_f = out_f - in_f
        _clipitem(
            v1, f"clipitem-a{clip_idx}", seg.source_path,
            file_ids, declared, spec.fps, timebase, ntsc,
            start=cursor, end=cursor + dur_f, in_=in_f, out=out_f,
        )
        cursor += dur_f

    if spec.broll:
        v2 = ET.SubElement(video, "track")
        for clip_idx, ins in enumerate(spec.broll, start=1):
            in_f = _frames(ins.clip_in_sec, spec.fps)
            out_f = _frames(ins.clip_out_sec, spec.fps)
            dur_f = out_f - in_f
            if dur_f <= 0:
                continue
            offset_f = _frames(ins.aroll_offset_sec, spec.fps)
            _clipitem(
                v2, f"clipitem-b{clip_idx}", ins.source_path,
                file_ids, declared, spec.fps, timebase, ntsc,
                start=offset_f, end=offset_f + dur_f, in_=in_f, out=out_f,
            )

    _write_xml(xmeml, output_path)


def _rate(fps: float) -> tuple[int, str]:
    for key, val in _RATE_TABLE.items():
        if abs(fps - key) < 0.01:
            return val
    return (int(round(fps)), "FALSE")


def _frames(seconds: float, fps: float) -> int:
    if seconds <= 0:
        return 0
    return int(round(seconds * fps))


def _rate_block(parent: ET.Element, timebase: int, ntsc: str) -> None:
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(timebase)
    ET.SubElement(rate, "ntsc").text = ntsc


def _timecode_block(parent: ET.Element, timebase: int, ntsc: str) -> None:
    tc = ET.SubElement(parent, "timecode")
    _rate_block(tc, timebase, ntsc)
    ET.SubElement(tc, "string").text = "00:00:00:00"
    ET.SubElement(tc, "frame").text = "0"
    ET.SubElement(tc, "displayformat").text = "NDF"


def _format_block(parent: ET.Element, spec: SequenceSpec) -> None:
    fmt = ET.SubElement(parent, "format")
    samp = ET.SubElement(fmt, "samplecharacteristics")
    ET.SubElement(samp, "width").text = str(spec.width)
    ET.SubElement(samp, "height").text = str(spec.height)


def _clipitem(
    track: ET.Element, clip_id: str, source_path: Path,
    file_ids: dict[Path, str], declared: set[Path],
    fps: float, timebase: int, ntsc: str,
    *, start: int, end: int, in_: int, out: int,
) -> None:
    item = ET.SubElement(track, "clipitem", {"id": clip_id})
    ET.SubElement(item, "name").text = Path(source_path).stem
    ET.SubElement(item, "duration").text = str(out - in_)
    _rate_block(item, timebase, ntsc)
    ET.SubElement(item, "start").text = str(start)
    ET.SubElement(item, "end").text = str(end)
    ET.SubElement(item, "in").text = str(in_)
    ET.SubElement(item, "out").text = str(out)
    file_id = file_ids[source_path]
    if source_path in declared:
        ET.SubElement(item, "file", {"id": file_id})
    else:
        declared.add(source_path)
        file_el = ET.SubElement(item, "file", {"id": file_id})
        ET.SubElement(file_el, "name").text = Path(source_path).name
        ET.SubElement(file_el, "pathurl").text = Path(source_path).resolve().as_uri()
        _rate_block(file_el, timebase, ntsc)
        ET.SubElement(file_el, "duration").text = str(out)


def _write_xml(root: ET.Element, output_path: Path) -> None:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n",
        encoding="utf-8",
    )
