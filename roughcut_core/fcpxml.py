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

Times are rational fractions snapped to the sequence frame rate so
Premiere and Resolve relink cleanly. Asset durations are derived from
the maximum referenced out-point per source — accurate enough for the
editor to scrub, and keeps this module ffmpeg-free.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from math import gcd
from pathlib import Path

from roughcut_core.models import AngleSelection, SequenceSpec

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


def write_fcpxml(spec: SequenceSpec, output_path: Path) -> None:
    fps_num, fps_den = _fps_rational(spec.fps)
    fcpxml = ET.Element("fcpxml", {"version": "1.10"})

    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(resources, "format", {
        "id": "r0",
        "name": _format_name(spec.fps, spec.height),
        "frameDuration": f"{fps_den}/{fps_num}s",
        "width": str(spec.width),
        "height": str(spec.height),
    })

    asset_ids = _emit_assets(resources, spec, fps_num, fps_den)

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": spec.name})
    project = ET.SubElement(event, "project", {"name": spec.name})

    total_dur = sum(seg.out_sec - seg.in_sec for seg in spec.aroll)
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
    for seg in spec.aroll:
        aid = asset_ids[seg.source_path]
        dur = seg.out_sec - seg.in_sec
        clip = ET.SubElement(spine, "asset-clip", {
            "ref": aid,
            "offset": _t(cursor, fps_num, fps_den),
            "name": seg.source_path.stem,
            "start": _t(seg.in_sec, fps_num, fps_den),
            "duration": _t(dur, fps_num, fps_den),
            "tcFormat": "NDF",
        })
        _attach_broll(clip, spec, asset_ids, cursor, dur, fps_num, fps_den)
        cursor += dur

    _write(fcpxml, output_path)


def _attach_broll(parent: ET.Element, spec: SequenceSpec, asset_ids: dict[Path, str],
                  base_offset: float, parent_dur: float, fps_num: int, fps_den: int) -> None:
    """Nest b-roll asset-clips on lane 1 of the A-roll clip they fall inside."""
    for ins in spec.broll:
        if not (base_offset <= ins.aroll_offset_sec < base_offset + parent_dur):
            continue
        broll_aid = asset_ids.get(ins.source_path)
        if broll_aid is None:
            continue
        local_offset = ins.aroll_offset_sec - base_offset
        bduration = max(0.0, ins.clip_out_sec - ins.clip_in_sec)
        if bduration <= 0:
            continue
        ET.SubElement(parent, "asset-clip", {
            "ref": broll_aid,
            "lane": "1",
            "offset": _t(local_offset, fps_num, fps_den),
            "name": ins.source_path.stem,
            "start": _t(ins.clip_in_sec, fps_num, fps_den),
            "duration": _t(bduration, fps_num, fps_den),
        })


def _emit_assets(resources: ET.Element, spec: SequenceSpec,
                 fps_num: int, fps_den: int) -> dict[Path, str]:
    """Register one <asset> per unique source path. Returns {path: asset_id}."""
    paths: list[Path] = []
    seen: set[Path] = set()
    for seg in spec.aroll:
        if seg.source_path not in seen:
            seen.add(seg.source_path)
            paths.append(seg.source_path)
    for ins in spec.broll:
        if ins.source_path not in seen:
            seen.add(ins.source_path)
            paths.append(ins.source_path)

    asset_ids: dict[Path, str] = {}
    for i, path in enumerate(paths, start=1):
        aid = f"r{i}"
        asset_ids[path] = aid
        dur = _asset_duration(path, spec)
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


def _asset_duration(path: Path, spec: SequenceSpec) -> float:
    """Pick the largest out-point referenced for this source (a-roll or b-roll)."""
    candidates = [seg.out_sec for seg in spec.aroll if seg.source_path == path]
    candidates += [ins.clip_out_sec for ins in spec.broll if ins.source_path == path]
    return max(candidates, default=60.0)


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


def write_multicam_fcpxml(
    selections: list[AngleSelection],
    output_path: Path,
    *,
    name: str = "roughcut-multicam",
    fps: float = 23.976,
    width: int = 1920,
    height: int = 1080,
) -> None:
    """Emit a flat V1 sequence with one asset-clip per angle decision.

    This is intentionally NOT a true `<mc-clip>` multicam element — the
    agent has already picked angles, so we lay them out as straight cuts
    Premiere/Resolve can scrub. Editors who want to re-pick angles in
    the NLE can re-link sources after import.
    """
    if not selections:
        raise ValueError("write_multicam_fcpxml: selections is empty")
    fps_num, fps_den = _fps_rational(fps)
    fcpxml = ET.Element("fcpxml", {"version": "1.10"})
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(resources, "format", {
        "id": "r0",
        "name": _format_name(fps, height),
        "frameDuration": f"{fps_den}/{fps_num}s",
        "width": str(width),
        "height": str(height),
    })

    paths: list[Path] = []
    seen: set[Path] = set()
    for s in selections:
        if s.clip_path not in seen:
            seen.add(s.clip_path)
            paths.append(s.clip_path)
    asset_ids: dict[Path, str] = {}
    for i, p in enumerate(paths, start=1):
        aid = f"r{i}"
        asset_ids[p] = aid
        dur = max(s.clip_out_sec for s in selections if s.clip_path == p)
        ET.SubElement(resources, "asset", {
            "id": aid, "name": Path(p).stem, "src": Path(p).resolve().as_uri(),
            "start": "0s", "duration": _t(dur, fps_num, fps_den),
            "hasVideo": "1", "hasAudio": "1", "format": "r0",
            "videoSources": "1", "audioSources": "1",
            "audioChannels": "2", "audioRate": "48000",
        })

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": name})
    project = ET.SubElement(event, "project", {"name": name})
    total = max(s.timeline_out_sec for s in selections)
    seq_elem = ET.SubElement(project, "sequence", {
        "format": "r0",
        "duration": _t(total, fps_num, fps_den),
        "tcStart": "0s", "tcFormat": "NDF",
        "audioLayout": "stereo", "audioRate": "48k",
    })
    spine = ET.SubElement(seq_elem, "spine")
    for s in selections:
        dur = s.timeline_out_sec - s.timeline_in_sec
        if dur <= 0:
            continue
        ET.SubElement(spine, "asset-clip", {
            "ref": asset_ids[s.clip_path],
            "offset": _t(s.timeline_in_sec, fps_num, fps_den),
            "name": f"{Path(s.clip_path).stem} ({s.reason})",
            "start": _t(s.clip_in_sec, fps_num, fps_den),
            "duration": _t(dur, fps_num, fps_den),
            "tcFormat": "NDF",
        })
    _write(fcpxml, output_path)
