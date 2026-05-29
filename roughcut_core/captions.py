"""Semantic b-roll caption cache.

Today the agent must `extract_frame_grid` each b-roll clip every session
and vision-read every contact sheet to remember what's in it. v0.8.0
caches the description so it's re-usable forever — when the agent picks
b-roll for a segment, it calls `search_broll(query)` against the cache
instead of re-reading 30 contact sheets.

The cache lives at `cache/captions/<sha-of-resolved-path>.json` keyed
by `transcribe.cache_key(path)` (mirrors the transcript cache pattern,
so the same hash identifies the same file across all roughcut subsystems).

Architecture: agent-vision captions (no LLM in our code). The agent
calls `extract_frame_grid` on a b-roll clip, vision-reads it, then
calls `describe_clip(video_path, description, tags=[...], mood=...)`
to persist its summary. roughcut just stores and searches.

This is the "Pictory / Opus Clip" moat — once your library is captioned,
the agent can match b-roll to A-roll content semantically without
re-doing the vision work.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from roughcut_core import transcribe


def write_caption(
    video_path: Path,
    cache_dir: Path,
    description: str,
    tags: list[str] | None = None,
    mood: str | None = None,
) -> dict:
    """Persist a caption for `video_path`. Returns the stored record.

    Subsequent `write_caption` calls overwrite the prior caption — the
    agent might re-describe the same clip after seeing a higher-quality
    frame grid. The cache key is path-stable (mtime-independent) so
    moving the file invalidates the entry but editing the file content
    doesn't.
    """
    video_path = Path(video_path).resolve(strict=False)
    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    record = {
        "video_path": str(video_path),
        "description": description.strip(),
        "tags": [t.lower().strip() for t in (tags or []) if t.strip()],
        "mood": (mood or "").strip().lower() or None,
        "captioned_at": time.time(),
        "cache_key": _cache_key(video_path),
    }
    path = _caption_path(cache_dir, video_path)
    _atomic_write_json(path, record)
    return record


def read_caption(video_path: Path, cache_dir: Path) -> dict | None:
    """Return the cached caption for `video_path`, or None if uncached."""
    video_path = Path(video_path).resolve(strict=False)
    path = _caption_path(cache_dir, video_path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def search_captions(
    query: str,
    cache_dir: Path,
    folder_path: Path | None = None,
    max_results: int = 10,
) -> dict:
    """Case-insensitive substring search across cached captions.

    Matches against description text + tags. If `folder_path` is set,
    only captions whose `video_path` lives under that folder are
    returned. Each hit reports the full caption record so the agent
    can rank by tag / mood / description without a follow-up call.
    """
    needle = (query or "").lower().strip()
    if not needle:
        return {
            "query": query, "folder_path": str(folder_path) if folder_path else None,
            "captions_searched": 0, "result_count": 0, "results": [],
        }

    captions_root = Path(cache_dir) / "captions"
    if not captions_root.is_dir():
        return {
            "query": query, "folder_path": str(folder_path) if folder_path else None,
            "captions_searched": 0, "result_count": 0, "results": [],
        }

    hits: list[dict] = []
    searched = 0
    for path in sorted(captions_root.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        searched += 1
        if folder_path and not _under(rec.get("video_path", ""), folder_path):
            continue
        if _matches(rec, needle):
            hits.append(rec)
        if len(hits) >= max_results:
            break

    return {
        "query": query,
        "folder_path": str(folder_path) if folder_path else None,
        "captions_searched": searched,
        "result_count": len(hits),
        "results": hits,
    }


def list_captions(cache_dir: Path, folder_path: Path | None = None) -> dict:
    """Return every cached caption, optionally filtered by source folder."""
    captions_root = Path(cache_dir) / "captions"
    out: list[dict] = []
    if captions_root.is_dir():
        for path in sorted(captions_root.glob("*.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if folder_path and not _under(rec.get("video_path", ""), folder_path):
                continue
            out.append(rec)
    return {
        "folder_path": str(folder_path) if folder_path else None,
        "caption_count": len(out),
        "captions": out,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _cache_key(path: Path) -> str:
    """Mirror transcribe.cache_key (abs path + size + mtime) for stability."""
    return transcribe.cache_key(path)


def _caption_path(cache_dir: Path, video_path: Path) -> Path:
    out_dir = Path(cache_dir) / "captions"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{_cache_key(video_path)}.json"


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _matches(rec: dict, needle: str) -> bool:
    if needle in (rec.get("description") or "").lower():
        return True
    for tag in rec.get("tags") or []:
        if needle in tag.lower():
            return True
    if needle in (rec.get("mood") or ""):
        return True
    return False


def _under(child: str, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False
