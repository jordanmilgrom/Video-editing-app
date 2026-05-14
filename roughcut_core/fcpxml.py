"""FCPXML v1.10 emission.

Structure:

```
fcpxml
  resources
    format          # one shared sequence format (r0) + per-source asset formats
    asset...        # one per unique source file, absolute file:// URI
  library/event/project/sequence/spine
    asset-clip      # A-roll on lane 0 (V1)
      asset-clip lane=1  # b-roll on V2, nested inside its A-roll parent
```

Times are rational fractions snapped to the sequence frame rate so
Premiere and Resolve relink cleanly.

v0.6.5 hardening:
- Asset dedup keys on `Path.resolve()` so `/foo/clip.mov` and
  `/foo/./clip.mov` collapse into one asset. Without this Final Cut
  Pro can see two assets pointing to the same media-rep and reject
  the edit with "Invalid edit with no respective media".
- One `<format>` is emitted per unique source dimensions/frame-rate
  (best-effort via ffprobe). Falls back to the sequence format when
  ffprobe is unavailable. No more hardcoded 1920x1080/23.976 globally.
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

    asset_ids, source_for_aroll, source_for_broll = _emit_assets(
        resources, spec, fps_num, fps_den
    )

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
    for seg_idx, seg in enumerate(spec.aroll):
        aid = asset_ids[source_for_aroll[seg_idx]]
        dur = seg.out_sec - seg.in_sec
        clip = ET.SubElement(spine, "asset-clip", {
            "ref": aid,
            "offset": _t(cursor, fps_num, fps_den),
            "name": Path(seg.source_path).stem,
            "start": _t(seg.in_sec, fps_num, fps_den),
            "duration": _t(dur, fps_num, fps_den),
            "tcFormat": "NDF",
        })
        _attach_broll(clip, spec, asset_ids, source_for_broll,
                      cursor, dur, fps_num, fps_den)
        cursor += dur

    _write(fcpxml, output_path)


def _attach_broll(
    parent: ET.Element, spec: SequenceSpec, asset_ids: dict[Path, str],
    source_for_broll: list[Path],
    base_offset: float, parent_dur: float, fps_num: int, fps_den: int,
) -> None:
    """Nest b-roll asset-clips on lane 1 of the A-roll clip they fall inside."""
    for ins_idx, ins in enumerate(spec.broll):
        if not (base_offset <= ins.aroll_offset_sec < base_offset + parent_dur):
            continue
        broll_aid = asset_ids.get(source_for_broll[ins_idx])
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
            "name": Path(ins.source_path).stem,
            "start": _t(ins.clip_in_sec, fps_num, fps_den),
            "duration": _t(bduration, fps_num, fps_den),
        })


def _emit_assets(
    resources: ET.Element, spec: SequenceSpec, fps_num: int, fps_den: int,
) -> tuple[dict[Path, str], list[Path], list[Path]]:
    """Register one <asset> per unique RESOLVED source path.

    Returns (asset_ids, source_for_aroll, source_for_broll) where the
    two lists give the resolved key the caller should look up for each
    segment / insert. Storing the resolved key per-segment keeps the
    spine emitter from re-resolving paths (which can race on slow disks).
    """
    source_for_aroll: list[Path] = []
    source_for_broll: list[Path] = []
    paths: list[Path] = []
    seen: set[Path] = set()
    for seg in spec.aroll:
        key = _resolve_for_dedup(seg.source_path)
        source_for_aroll.append(key)
        if key not in seen:
            seen.add(key)
            paths.append(key)
    for ins in spec.broll:
        key = _resolve_for_dedup(ins.source_path)
        source_for_broll.append(key)
        if key not in seen:
            seen.add(key)
            paths.append(key)

    asset_ids: dict[Path, str] = {}
    # Per-source dimensions/fps via ffprobe (best-effort). Fall back to
    # the sequence values when ffprobe isn't on PATH or the source isn't
    # readable yet (e.g. unit tests that touch() a stub file).
    per_source = _per_source_format(paths, spec)
    fmt_cache: dict[tuple[int, int, int, int], str] = {}
    next_fmt_id = 1
    # Asset ids use a separate `a{i}` namespace so they can never
    # collide with the `r0` sequence format or `sf{i}` per-source formats.
    for i, path in enumerate(paths, start=1):
        aid = f"a{i}"
        asset_ids[path] = aid
        meta = per_source.get(path)
        if meta is None:
            fmt_ref = "r0"
        else:
            w, h, fnum, fden = meta
            fmt_key = (w, h, fnum, fden)
            fmt_ref = fmt_cache.get(fmt_key)
            if fmt_ref is None:
                fmt_ref = f"sf{next_fmt_id}"
                next_fmt_id += 1
                fmt_cache[fmt_key] = fmt_ref
                ET.SubElement(resources, "format", {
                    "id": fmt_ref,
                    "name": _format_name(fnum / fden if fden else spec.fps, h),
                    "frameDuration": f"{fden}/{fnum}s",
                    "width": str(w),
                    "height": str(h),
                })
        dur = _asset_duration(path, spec)
        asset = ET.SubElement(resources, "asset", {
            "id": aid,
            "name": path.stem,
            "start": "0s",
            "duration": _t(dur, fps_num, fps_den),
            "hasVideo": "1",
            "hasAudio": "1",
            "format": fmt_ref,
            "videoSources": "1",
            "audioSources": "1",
            "audioChannels": "2",
            "audioRate": "48000",
        })
        # DTD 1.10 requires `<media-rep>` as a child; `src` on the asset
        # element itself was deprecated long ago and the validator rejects
        # the document outright (no declaration for attribute src of asset).
        ET.SubElement(asset, "media-rep", {
            "kind": "original-media",
            "src": Path(path).as_uri(),
        })
    return asset_ids, source_for_aroll, source_for_broll


def _resolve_for_dedup(p: Path | str) -> Path:
    """Normalize a source path to a canonical key for asset dedup.

    Uses `resolve(strict=False)` so unit tests with stubbed paths still
    pass while real-world paths (with `.`, `..`, symlinks, or unusual
    duplication) collapse correctly. Without this, an agent that hands
    us the same file under two surface representations gets two assets
    pointing at the same media-rep and FCP rejects the import.
    """
    return Path(p).resolve(strict=False)


def _per_source_format(
    paths: list[Path], spec: SequenceSpec,
) -> dict[Path, tuple[int, int, int, int]]:
    """Best-effort ffprobe of each unique source: (w, h, fps_num, fps_den).

    ffprobe failure is silent — we just fall back to the sequence
    format. The point isn't perfection; it's avoiding the long-running
    "everything is 1920x1080 23.976" lie when the agent feeds in 4K
    drone clips alongside a 1080p interview.
    """
    from roughcut_core import clips
    out: dict[Path, tuple[int, int, int, int]] = {}
    for p in paths:
        try:
            meta = clips.probe_clip(p)
        except Exception:  # noqa: BLE001
            continue
        if meta.width <= 0 or meta.height <= 0 or meta.fps <= 0:
            continue
        fnum, fden = _fps_rational(meta.fps)
        out[p] = (meta.width, meta.height, fnum, fden)
    return out


def _asset_duration(path: Path, spec: SequenceSpec) -> float:
    """Pick the largest out-point referenced for this source (a-roll or b-roll)."""
    candidates = [
        seg.out_sec for seg in spec.aroll
        if _resolve_for_dedup(seg.source_path) == path
    ]
    candidates += [
        ins.clip_out_sec for ins in spec.broll
        if _resolve_for_dedup(ins.source_path) == path
    ]
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

    keys_for_sel: list[Path] = [_resolve_for_dedup(s.clip_path) for s in selections]
    paths: list[Path] = []
    seen: set[Path] = set()
    for k in keys_for_sel:
        if k not in seen:
            seen.add(k)
            paths.append(k)
    asset_ids: dict[Path, str] = {}
    for i, p in enumerate(paths, start=1):
        aid = f"r{i}"
        asset_ids[p] = aid
        dur = max(
            s.clip_out_sec for j, s in enumerate(selections)
            if keys_for_sel[j] == p
        )
        asset = ET.SubElement(resources, "asset", {
            "id": aid, "name": Path(p).stem,
            "start": "0s", "duration": _t(dur, fps_num, fps_den),
            "hasVideo": "1", "hasAudio": "1", "format": "r0",
            "videoSources": "1", "audioSources": "1",
            "audioChannels": "2", "audioRate": "48000",
        })
        ET.SubElement(asset, "media-rep", {
            "kind": "original-media",
            "src": Path(p).as_uri(),
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
    for j, s in enumerate(selections):
        dur = s.timeline_out_sec - s.timeline_in_sec
        if dur <= 0:
            continue
        ET.SubElement(spine, "asset-clip", {
            "ref": asset_ids[keys_for_sel[j]],
            "offset": _t(s.timeline_in_sec, fps_num, fps_den),
            "name": f"{Path(s.clip_path).stem} ({s.reason})",
            "start": _t(s.clip_in_sec, fps_num, fps_den),
            "duration": _t(dur, fps_num, fps_den),
            "tcFormat": "NDF",
        })
    _write(fcpxml, output_path)
