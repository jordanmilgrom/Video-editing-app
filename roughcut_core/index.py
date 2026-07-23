"""One-call project inventory: everything an agent needs to survey a shoot.

`index_project(folder)` returns a compact entry per clip that composes
existing per-clip capabilities:

  - `clips.probe_clip` for duration / resolution / audio presence
  - `documentary.lookup_transcript_by_video_path` for transcript status
  - `documentary.summarize_clip` for opening / closing / longest-segment
  - `captions.read_caption` for any prior human/agent-supplied b-roll caption

The idea: replace 32 tool calls (list_clips, then per-clip
summarize / lookup / caption fetches) with ONE call whose response is
small enough to fit in a single turn. The agent can then decide which
clips to read in full, transcribe, or caption without the round-trip
tax.

Deterministic. No LLM imports. Cache-friendly — every underlying call
already caches its own state, so re-indexing a folder that hasn't
changed is close to free.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roughcut_core import captions, clips, documentary, scene_analysis


def index_project(
    folder: Path,
    cache_dir: Path,
    *,
    recursive: bool = True,
    include_top_segments: int = 2,
) -> dict[str, Any]:
    """Return one compact entry per video clip in `folder`.

    Each entry surfaces:
      - `filename`, `path`, `duration_sec`, `resolution`, `has_audio`
      - `transcript`: `{status: "cached" | "missing", transcript_path,
        segment_count, opening_200_chars, longest_segment_text,
        top_segments: [{text, start_sec, end_sec}]}`
      - `caption`: the cached agent-vision caption (if any) — description,
        tags, mood

    `include_top_segments` (default 2) controls how many of the longest
    transcript segments are inlined as quick-scan quotes.
    """
    folder = Path(folder)
    cache_dir = Path(cache_dir)
    if not folder.is_dir():
        raise NotADirectoryError(folder)

    entries: list[dict[str, Any]] = []
    total_duration = 0.0
    transcribed_count = 0
    silent_count = 0
    captioned_count = 0
    scene_analyzed_count = 0

    for meta in clips.list_clips(folder, recursive=recursive):
        entry: dict[str, Any] = {
            "filename": meta.path.name,
            "path": str(meta.path),
            "duration_sec": round(meta.duration_sec, 3),
            "resolution": f"{meta.width}x{meta.height}",
            "fps": round(meta.fps, 3),
            "has_audio": meta.has_audio,
            "size_bytes": meta.size_bytes,
        }
        total_duration += meta.duration_sec
        if not meta.has_audio:
            silent_count += 1

        # Transcript summary (if cached).
        try:
            hit = documentary.lookup_transcript_by_video_path(meta.path, cache_dir)
        except Exception:  # noqa: BLE001
            hit = {"found": False}
        if hit.get("found"):
            transcribed_count += 1
            best = hit["best_match"]
            try:
                summary = documentary.summarize_clip(Path(best["transcript_path"]))
                top_segments = _top_segments(
                    Path(best["transcript_path"]), n=include_top_segments,
                )
                entry["transcript"] = {
                    "status": "cached",
                    "transcript_path": best["transcript_path"],
                    "segment_count": summary["segment_count"],
                    "total_speech_chars": summary["total_speech_chars"],
                    "opening_200_chars": summary["opening_200_chars"],
                    "longest_segment_text": summary["longest_segment_text"],
                    "top_segments": top_segments,
                }
            except Exception:  # noqa: BLE001
                entry["transcript"] = {
                    "status": "cached",
                    "transcript_path": best["transcript_path"],
                    "note": "summarize failed",
                }
        else:
            entry["transcript"] = {
                "status": "silent" if not meta.has_audio else "missing",
                "hint": (
                    "clip has no audio stream" if not meta.has_audio
                    else "call transcribe_video(video_path=...) to add"
                ),
            }

        # Caption (if any).
        try:
            caption = captions.read_caption(meta.path, cache_dir)
        except Exception:  # noqa: BLE001
            caption = None
        if caption is not None:
            captioned_count += 1
            entry["caption"] = {
                "description": caption.get("description"),
                "tags": caption.get("tags", []),
                "mood": caption.get("mood"),
            }

        # v0.12: structured scene analysis (if agent has authored one)
        try:
            sa = scene_analysis.read_scene_analysis(meta.path, cache_dir)
        except Exception:  # noqa: BLE001
            sa = None
        if sa is not None:
            scene_analyzed_count += 1
            entry["scene_analysis"] = {
                "one_line": sa.one_line,
                "shot_count": sa.shot_count,
                "usability_verdict": sa.usability_verdict,
                "quality_issues": sa.quality_issues,
                "color_palette": sa.color_palette,
                "is_blooper": sa.is_blooper,
                "is_retake": sa.is_retake,
                "tags": sa.tags,
            }

        entries.append(entry)

    return {
        "folder": str(folder),
        "recursive": recursive,
        "clip_count": len(entries),
        "total_duration_sec": round(total_duration, 3),
        "transcribed_count": transcribed_count,
        "silent_count": silent_count,
        "captioned_count": captioned_count,
        "scene_analyzed_count": scene_analyzed_count,
        "clips": entries,
    }


def _top_segments(transcript_path: Path, n: int) -> list[dict[str, Any]]:
    """Return the top N longest transcript segments as quick-scan quotes."""
    if n <= 0:
        return []
    from roughcut_core.models import Transcript
    t = Transcript.model_validate_json(
        transcript_path.read_text(encoding="utf-8")
    )
    ranked = sorted(t.segments, key=lambda s: len(s.text), reverse=True)
    return [
        {
            "text": s.text.strip(),
            "start_sec": round(s.start, 3),
            "end_sec": round(s.end, 3),
        }
        for s in ranked[:n]
    ]
