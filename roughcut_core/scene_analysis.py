"""Persistent structured scene analysis — the 'AI's memory of what's in a clip'.

Level 2 of the multi-scale video understanding stack:

  Level 1 — Deterministic frame/audio metrics (analyze_motion, find_audio_silences).
  Level 2 — Structured shot-by-shot description authored by the agent from
            vision + audio (this module). Cached forever.
  Level 3 — Sequence-level judgment (judge_cut, gap-fill, blooper-detection)
            layered on top of Level 2. Coming later.

The agent's workflow:

  1. Call `analyze_scene(video_path)` → server assembles a bundle:
       - contact sheet of key frames
       - motion spans + cut boundaries (from motion.analyze_motion)
       - audio envelope stats
       - transcript window per candidate shot (if cached)
       - the SceneAnalysis JSON schema, with an empty template
  2. Agent vision-reads the sheet, fills in the template
  3. Agent calls `save_scene_analysis(video_path, analysis)` → cached

Once cached, `read_scene_analysis` and `search_scenes` make the analysis
queryable across sessions. `index_project` surfaces the analysis inline
so a fresh session can survey a shoot without re-doing vision.

Deterministic layer. No LLM imports. The agent brings the reasoning.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Shot(BaseModel):
    """One shot within a clip — the semantic unit the agent describes.

    Values for `type`, `camera`, `quality` are strings (not enums) so the
    agent can extend the vocabulary as needed. Suggested vocabularies:

    - type: wide / medium / close-up / extreme-close-up / over-shoulder /
            insert / cutaway / b-roll / graphic / title
    - camera: static / slow_pan / fast_pan / tilt / zoom_in / zoom_out /
              handheld / dolly / crane / cut
    - quality: clean / soft_focus / shaky / underexposed / overexposed /
               blooper / retake / usable-with-crop
    """

    shot_index: int
    start_sec: float
    end_sec: float
    type: str
    subject: str = ""
    camera: str = "static"
    composition: str = ""
    color_mood: str = ""
    quality: str = "clean"
    notable_events: list[str] = Field(default_factory=list)


class SceneAnalysis(BaseModel):
    """Structured description of what a video clip contains.

    The agent authors this after vision-reading the analyze_scene bundle.
    Everything downstream (judge_cut, can_fill_gap, gap-fill matching)
    consumes THIS, not the raw video, so quality of editorial decisions is
    proportional to quality of this description.
    """

    video_path: str
    video_hash: str
    duration_sec: float
    one_line: str                 # "wide shot of factory floor, workers moving"
    shot_count: int
    shots: list[Shot]
    usability_verdict: str = ""   # "good cutaway, avoid 8-9s (bump)"
    quality_issues: list[str] = Field(default_factory=list)
    color_palette: str = ""
    is_blooper: bool = False
    is_retake: bool = False
    tags: list[str] = Field(default_factory=list)
    created_at: float = 0.0


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------


def _cache_key(video_path: Path) -> str:
    """Content-addressed key mirroring transcribe.cache_key semantics."""
    p = Path(video_path).resolve(strict=False)
    try:
        st = p.stat()
        raw = f"{p}:{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        raw = str(p)
    return hashlib.sha256(raw.encode()).hexdigest()


def _analysis_path(cache_dir: Path, video_path: Path) -> Path:
    return Path(cache_dir) / "scene-analyses" / f"{_cache_key(video_path)}.json"


def write_scene_analysis(
    video_path: Path, cache_dir: Path, analysis: SceneAnalysis,
) -> Path:
    """Atomically persist `analysis` under the video's cache key.

    Overwrites any prior analysis for the same clip (agents refine their
    read after seeing more frames — that's expected).
    """
    out = _analysis_path(cache_dir, video_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not analysis.created_at:
        analysis.created_at = time.time()
    if not analysis.video_hash:
        analysis.video_hash = _cache_key(video_path)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, out)
    return out


def read_scene_analysis(
    video_path: Path, cache_dir: Path,
) -> SceneAnalysis | None:
    """Return the cached analysis for `video_path`, or None if not present."""
    p = _analysis_path(cache_dir, video_path)
    if not p.is_file():
        return None
    try:
        return SceneAnalysis.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def list_scene_analyses(
    cache_dir: Path, folder_path: Path | None = None,
) -> list[SceneAnalysis]:
    """Enumerate every cached analysis, optionally scoped to a source folder."""
    root = Path(cache_dir) / "scene-analyses"
    if not root.is_dir():
        return []
    out: list[SceneAnalysis] = []
    for p in sorted(root.glob("*.json"), key=lambda x: -x.stat().st_mtime):
        try:
            a = SceneAnalysis.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if folder_path is not None:
            try:
                Path(a.video_path).resolve().relative_to(
                    Path(folder_path).resolve()
                )
            except ValueError:
                continue
        out.append(a)
    return out


def search_scene_analyses(
    query: str, cache_dir: Path,
    folder_path: Path | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    """Case-insensitive substring search across cached analyses.

    Matches against `one_line`, `usability_verdict`, `color_palette`,
    every shot's `subject` / `composition` / `notable_events`, and any
    top-level `tags`. Returns hits sorted by match count desc.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return {"query": query, "result_count": 0, "results": []}

    hits: list[tuple[int, SceneAnalysis, list[str]]] = []
    for a in list_scene_analyses(cache_dir, folder_path):
        haystack: list[tuple[str, str]] = [
            ("one_line", a.one_line),
            ("usability", a.usability_verdict),
            ("palette", a.color_palette),
        ]
        haystack.extend(("tag", t) for t in a.tags)
        for s in a.shots:
            haystack.append(("subject", s.subject))
            haystack.append(("composition", s.composition))
            haystack.append(("color_mood", s.color_mood))
            haystack.append(("quality", s.quality))
            for ev in s.notable_events:
                haystack.append(("event", ev))
        matched = [
            f"{field}: {value}" for field, value in haystack
            if value and needle in value.lower()
        ]
        if matched:
            hits.append((len(matched), a, matched))

    hits.sort(key=lambda t: -t[0])
    hits = hits[: max(1, min(100, int(max_results or 20)))]
    return {
        "query": query,
        "result_count": len(hits),
        "results": [
            {
                "video_path": a.video_path,
                "match_count": n,
                "matched_snippets": snippets[:6],
                "one_line": a.one_line,
                "shot_count": a.shot_count,
                "duration_sec": a.duration_sec,
                "is_blooper": a.is_blooper,
                "is_retake": a.is_retake,
            }
            for (n, a, snippets) in hits
        ],
    }


# ---------------------------------------------------------------------------
# Bundle assembly — what analyze_scene returns to the agent
# ---------------------------------------------------------------------------


TEMPLATE_SCHEMA_HINT = {
    "one_line": "<one-sentence summary of the clip>",
    "shots": [
        {
            "shot_index": 0,
            "start_sec": 0.0,
            "end_sec": 0.0,
            "type": "wide|medium|close-up|cutaway|insert|b-roll|...",
            "subject": "<what/who is in frame>",
            "camera": "static|slow_pan|fast_pan|tilt|zoom_in|zoom_out|handheld|cut",
            "composition": "<rule-of-thirds, symmetric, centered, etc>",
            "color_mood": "<warm industrial, cool clinical, muted natural, etc>",
            "quality": "clean|soft_focus|shaky|underexposed|overexposed|blooper|retake",
            "notable_events": ["camera bump at X.Xs", "subject enters frame", "..."],
        },
    ],
    "usability_verdict": "<summary editorial recommendation>",
    "quality_issues": ["<clip-wide issues>"],
    "color_palette": "<dominant color feel>",
    "is_blooper": False,
    "is_retake": False,
    "tags": ["<optional single-word keywords>"],
}


def build_scene_bundle(
    video_path: Path, cache_dir: Path,
    *, sample_hz: float = 2.0, num_frames: int = 25,
) -> dict[str, Any]:
    """Assemble the multi-modal input the agent uses to write the analysis.

    Returns a dict with:
      - `contact_sheet_path`: `num_frames` frames spanning the whole clip
      - `motion`: analyze_motion output (spans, cut count, stable spans)
      - `shots_from_cuts`: shot boundaries derived from motion cuts
      - `audio_stats`: per-clip dBFS envelope summary (or None if no audio)
      - `transcript`: word-timed transcript if cached, else None
      - `duration_sec`, `has_audio`, `resolution`, `fps`
      - `schema_template`: the JSON schema the agent must fill in
      - `prior_analysis`: any existing cached analysis (agent can refine)
    """
    from roughcut_core import audio as _audio  # lazy — audio needs ffmpeg
    from roughcut_core import broll, clips, documentary, motion, transcribe

    video_path = Path(video_path)
    cache_dir = Path(cache_dir)

    meta = clips.probe_clip(video_path)
    duration = float(meta.duration_sec)

    # Contact sheet spanning the entire clip.
    sheet_dir = cache_dir / "scene-analysis-sheets"
    file_key = transcribe.cache_key(video_path)
    sheet_path = sheet_dir / f"{file_key}_{int(num_frames)}.jpg"
    if not sheet_path.exists():
        broll.build_contact_sheet(
            video_path, sheet_path, duration=duration,
            overlay_timecodes=True, num_frames=num_frames,
            tile_size=256, jpeg_quality=72,
        )

    motion_result: dict[str, Any] = {}
    try:
        motion_result = motion.analyze_motion(video_path, sample_hz=sample_hz)
    except Exception:  # noqa: BLE001
        motion_result = {"spans": [], "stable_spans": [], "cut_count": 0}

    shots_from_cuts = _cut_spans_to_shots(motion_result, duration)

    audio_stats: dict[str, Any] | None = None
    if meta.has_audio:
        try:
            import numpy as np
            env = _audio.decode_audio_envelope(video_path)
            if len(env) > 0:
                audio_stats = {
                    "duration_sec": round(len(env) * _audio.FRAME_MS / 1000, 3),
                    "min_db": round(float(np.min(env)), 2),
                    "mean_db": round(float(np.mean(env)), 2),
                    "max_db": round(float(np.max(env)), 2),
                    "silence_pct": round(
                        float(np.mean(env < -40.0)) * 100, 1),
                    "clipping_pct": round(
                        float(np.mean(env > -1.0)) * 100, 1),
                }
        except Exception:  # noqa: BLE001
            audio_stats = None

    transcript_bundle: dict[str, Any] | None = None
    try:
        hit = documentary.lookup_transcript_by_video_path(video_path, cache_dir)
        if hit.get("found"):
            best = hit["best_match"]
            from roughcut_core.models import Transcript
            t = Transcript.model_validate_json(
                Path(best["transcript_path"]).read_text(encoding="utf-8")
            )
            transcript_bundle = {
                "transcript_path": best["transcript_path"],
                "segment_count": len(t.segments),
                "duration_sec": round(t.duration, 3),
                "text": " ".join(s.text.strip() for s in t.segments).strip(),
                "segments": [
                    {
                        "start_sec": round(s.start, 3),
                        "end_sec": round(s.end, 3),
                        "text": s.text.strip(),
                    }
                    for s in t.segments
                ][:200],  # cap for byte budget
            }
    except Exception:  # noqa: BLE001
        transcript_bundle = None

    prior = read_scene_analysis(video_path, cache_dir)

    return {
        "video_path": str(video_path),
        "duration_sec": duration,
        "resolution": f"{meta.width}x{meta.height}",
        "fps": round(meta.fps, 3),
        "has_audio": meta.has_audio,
        "contact_sheet_path": str(sheet_path),
        "num_frames": num_frames,
        "sample_hz": sample_hz,
        "motion": {
            "cut_count": motion_result.get("cut_count", 0),
            "spans": motion_result.get("spans", []),
            "stable_spans": motion_result.get("stable_spans", []),
        },
        "shots_from_cuts": shots_from_cuts,
        "audio_stats": audio_stats,
        "transcript": transcript_bundle,
        "prior_analysis": prior.model_dump(mode="json") if prior else None,
        "schema_template": TEMPLATE_SCHEMA_HINT,
    }


def _cut_spans_to_shots(motion_result: dict, duration: float) -> list[dict]:
    """Derive shot windows from motion.analyze_motion's cut spans.

    Same algorithm as scenes.detect_scenes: cut spans are boundaries,
    the interval between them is a shot. Useful priors for the agent.
    """
    spans = motion_result.get("spans", []) or []
    boundaries = [0.0] + [
        s.get("start_sec", 0.0) for s in spans if s.get("label") == "cut"
    ] + [float(duration)]
    seen: set[float] = set()
    uniq: list[float] = []
    for b in sorted(boundaries):
        key = round(b, 3)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(key)

    shots: list[dict] = []
    for i in range(len(uniq) - 1):
        start = uniq[i]
        end = uniq[i + 1]
        if end - start < 0.2:
            continue
        shots.append({
            "shot_index": len(shots),
            "start_sec": start,
            "end_sec": end,
            "duration_sec": round(end - start, 3),
        })
    return shots
