"""FCPXML v1.10 emission.

Structure:

```
fcpxml
  resources
    format
    asset...        # one per unique source file, absolute file:// URI
  library/event/project/sequence/spine
    asset-clip      # A-roll on lane 0 (V1)
      asset-clip lane=1  # b-roll on V2, nested inside its A-roll parent
```

All times are rational fractions snapped to the sequence frame rate so
Premiere and Resolve relink cleanly.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from math import gcd
from pathlib import Path

from roughcut_core.models import Sequence

_FPS_TABLE = {
    23.976: (24000, 1001),
    24.0: (24, 1),
    25.0: (25, 1),
    29.97: (30000, 1001),
    30.0: (30, 1),
    50.0: (50, 1),
    59.94: (60000, 1001),
    60.0: (60, 1),
}


def write_fcpxml(sequence: Sequence, output_path: Path) -> None:
    fps_num, fps_den = _fps_rational(sequence.frame_rate)
    fcpxml = ET.Element("fcpxml", {"version": "1.10"})

    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(resources, "format", {
        "id": "r0",
        "name": _format_name(sequence.frame_rate, sequence.height),
        "frameDuration": f"{fps_den}/{fps_num}s",
        "width": str(sequence.width),
        "height": str(sequence.height),
    })

    asset_ids = _emit_assets(resources, sequence, fps_num, fps_den)

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": sequence.name})
    project = ET.SubElement(event, "project", {"name": sequence.name})

    aroll_takes = [tk for tk in sequence.takes if tk.chosen]
    total_dur = sum(tk.out_sec - tk.in_sec for tk in aroll_takes)
    seq_elem = ET.SubElement(project, "sequence", {
        "format": "r0",
        "duration": _t(total_dur, fps_num, fps_den),
        "tcStart": "0s",
        "tcFormat": "NDF",
        "audioLayout": "stereo",
        "audioRate": "48k",
    })
    spine = ET.SubElement(seq_elem, "spine")

    cursor = 0.0
    for tk in aroll_takes:
        aid = asset_ids[tk.source_path]
        dur = tk.out_sec - tk.in_sec
        clip = ET.SubElement(spine, "asset-clip", {
            "ref": aid,
            "offset": _t(cursor, fps_num, fps_den),
            "name": tk.source_path.stem,
            "start": _t(tk.in_sec, fps_num, fps_den),
            "duration": _t(dur, fps_num, fps_den),
            "tcFormat": "NDF",
        })
        _attach_broll(clip, sequence, asset_ids, cursor, dur, fps_num, fps_den)
        cursor += dur

    _write(fcpxml, output_path)


def _attach_broll(parent: ET.Element, sequence: Sequence, asset_ids: dict[Path, str],
                  base_offset: float, parent_dur: float, fps_num: int, fps_den: int) -> None:
    """Nest b-roll asset-clips on lane 1 of the A-roll clip they fall inside."""
    for m in sequence.broll:
        if not (base_offset <= m.aroll_offset_sec < base_offset + parent_dur):
            continue
        clip_meta = sequence.clips.get(m.clip_hash)
        if clip_meta is None:
            continue
        broll_aid = asset_ids.get(clip_meta.source_path)
        if broll_aid is None:
            continue
        local_offset = m.aroll_offset_sec - base_offset
        bduration = max(0.0, m.clip_out_sec - m.clip_in_sec)
        if bduration <= 0:
            continue
        ET.SubElement(parent, "asset-clip", {
            "ref": broll_aid,
            "lane": "1",
            "offset": _t(local_offset, fps_num, fps_den),
            "name": clip_meta.source_path.stem,
            "start": _t(m.clip_in_sec, fps_num, fps_den),
            "duration": _t(bduration, fps_num, fps_den),
        })


def _emit_assets(resources: ET.Element, sequence: Sequence,
                 fps_num: int, fps_den: int) -> dict[Path, str]:
    """Register one <asset> per unique source path. Returns {path: asset_id}."""
    paths: list[Path] = []
    seen: set[Path] = set()
    for tk in sequence.takes:
        if tk.chosen and tk.source_path not in seen:
            seen.add(tk.source_path)
            paths.append(tk.source_path)
    for m in sequence.broll:
        c = sequence.clips.get(m.clip_hash)
        if c and c.source_path not in seen:
            seen.add(c.source_path)
            paths.append(c.source_path)

    asset_ids: dict[Path, str] = {}
    for i, path in enumerate(paths, start=1):
        aid = f"r{i}"
        asset_ids[path] = aid
        dur = _asset_duration(path, sequence)
        ET.SubElement(resources, "asset", {
            "id": aid,
            "name": path.stem,
            "src": Path(path).resolve().as_uri(),
            "start": "0s",
            "duration": _t(dur, fps_num, fps_den),
            "hasVideo": "1",
            "hasAudio": "1",
            "format": "r0",
            "videoSources": "1",
            "audioSources": "1",
            "audioChannels": "2",
            "audioRate": "48000",
        })
    return asset_ids


def _asset_duration(path: Path, sequence: Sequence) -> float:
    for c in sequence.clips.values():
        if c.source_path == path:
            return c.duration
    return max(
        (tk.out_sec for tk in sequence.takes if tk.source_path == path), default=60.0,
    )


def _fps_rational(fps: float) -> tuple[int, int]:
    for key, (n, d) in _FPS_TABLE.items():
        if abs(fps - key) < 0.01:
            return n, d
    return (round(fps * 1000), 1000)


def _format_name(fps: float, height: int) -> str:
    if abs(fps - 23.976) < 0.01:
        suffix = "2398"
    elif abs(fps - 29.97) < 0.01:
        suffix = "2997"
    elif abs(fps - 59.94) < 0.01:
        suffix = "5994"
    else:
        suffix = str(int(round(fps)))
    return f"FFVideoFormat{height}p{suffix}"


def _t(seconds: float, fps_num: int, fps_den: int) -> str:
    """Snap seconds to a frame boundary; return rational like '1001/24000s'."""
    if seconds <= 0:
        return "0s"
    frames = round(seconds * fps_num / fps_den)
    num = frames * fps_den
    den = fps_num
    g = gcd(num, den) or 1
    return f"{num // g}/{den // g}s"


def _write(root: ET.Element, output_path: Path) -> None:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n",
        encoding="utf-8",
    )
