"""FCP 7 (XMEML v5) emitter — Premiere's most reliable import path.

Adobe Premiere imports FCPXML 1.10 spottily; the legacy `xmeml version="5"`
schema from Final Cut Pro 7 was the industry interchange format for a
decade, so Adobe wrote excellent (and stable) support for it. roughcut
emits a `.xml` next to the `.fcpxml` so editors who hit FCPXML problems
have a second path that works.

Layout:

    <xmeml version="5">
      <sequence>
        rate / timecode / media
          <video>
            <track>...A-roll clipitems...</track>
            <track>...B-roll clipitems on V2...</track>
          </video>

Times are in WHOLE FRAMES at the sequence rate. ntsc="TRUE" for the
.976 / 29.97 / 59.94 timebases.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from roughcut_core.models import SequenceSpec

# (timebase, ntsc) for each fps we care about.
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

    # File pool: one <file> per unique RESOLVED source path. v0.6.5: we
    # resolve before keying so two surface forms of the same file collapse
    # into one <file> id — same root cause and same fix as the FCPXML
    # emitter (P0 #1). Premiere accepts duplicate file ids less
    # gracefully than FCP, so this matters for the .xml path too.
    aroll_keys = [_resolve_for_dedup(s.source_path) for s in spec.aroll]
    broll_keys = [_resolve_for_dedup(ins.source_path) for ins in spec.broll]
    paths: list[Path] = []
    seen: set[Path] = set()
    for k in aroll_keys + broll_keys:
        if k not in seen:
            seen.add(k); paths.append(k)
    file_ids = {p: f"file-{i}" for i, p in enumerate(paths, start=1)}

    # V1: A-roll spine. Files declared inline on the first reference;
    # subsequent references use <file id="..."/> empty form per the schema.
    v1 = ET.SubElement(video, "track")
    declared: set[Path] = set()
    cursor = 0
    for clip_idx, seg in enumerate(spec.aroll, start=1):
        in_f = _frames(seg.in_sec, spec.fps)
        out_f = _frames(seg.out_sec, spec.fps)
        dur_f = out_f - in_f
        _clipitem(
            v1, f"clipitem-a{clip_idx}", aroll_keys[clip_idx - 1],
            file_ids, declared, spec.fps, timebase, ntsc,
            start=cursor, end=cursor + dur_f, in_=in_f, out=out_f,
        )
        cursor += dur_f

    # V2: B-roll inserts addressed by their A-roll-relative offset.
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
                v2, f"clipitem-b{clip_idx}", broll_keys[clip_idx - 1],
                file_ids, declared, spec.fps, timebase, ntsc,
                start=offset_f, end=offset_f + dur_f, in_=in_f, out=out_f,
            )

    _write_xml(xmeml, output_path)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


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
    track: ET.Element, clip_id: str, source_key: Path,
    file_ids: dict[Path, str], declared: set[Path],
    fps: float, timebase: int, ntsc: str,
    *, start: int, end: int, in_: int, out: int,
) -> None:
    item = ET.SubElement(track, "clipitem", {"id": clip_id})
    ET.SubElement(item, "name").text = source_key.stem
    ET.SubElement(item, "duration").text = str(out - in_)
    _rate_block(item, timebase, ntsc)
    ET.SubElement(item, "start").text = str(start)
    ET.SubElement(item, "end").text = str(end)
    ET.SubElement(item, "in").text = str(in_)
    ET.SubElement(item, "out").text = str(out)
    file_id = file_ids[source_key]
    if source_key in declared:
        # Reference-only form for subsequent uses of the same source.
        ET.SubElement(item, "file", {"id": file_id})
    else:
        declared.add(source_key)
        file_el = ET.SubElement(item, "file", {"id": file_id})
        ET.SubElement(file_el, "name").text = source_key.name
        # `as_uri()` percent-encodes spaces / `#` / `[` / `]` / etc., which is
        # the Premiere-correct form. v0.6.5 P0 #2.
        ET.SubElement(file_el, "pathurl").text = source_key.as_uri()
        _rate_block(file_el, timebase, ntsc)
        # Whole-file duration unknown without ffprobe; use the
        # max-referenced out as a lower bound. Premiere relinks happily.
        ET.SubElement(file_el, "duration").text = str(out)


def _resolve_for_dedup(p: Path | str) -> Path:
    """Mirror of `fcpxml._resolve_for_dedup` — see that docstring."""
    return Path(p).resolve(strict=False)


def _write_xml(root: ET.Element, output_path: Path) -> None:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n",
        encoding="utf-8",
    )
