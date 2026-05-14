"""Whisper model resolution + cache inventory.

Three things this module exists for:

1. Map short names (`small`, `medium`, `large-v3`, `large-v3-turbo`) to
   the mlx-community HF repo ids. Short names are what the agent and
   user think in; the HF id is what mlx-whisper expects.

2. Prefer a pre-bundled model on disk over an HF-cache lookup over a
   network fetch. The shipped .dxt drops whisper-small into
   `server/models/whisper-small/` at build time so first-run transcription
   has zero network round-trips.

3. Inventory: `get_system_status` reads `inventory()` to tell the agent
   which models are already on disk and which would trigger a download.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

SHORT_TO_HF: dict[str, str] = {
    "small":          "mlx-community/whisper-small-mlx",
    "medium":         "mlx-community/whisper-medium-mlx",
    "large-v3":       "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}

APPROX_SIZE_MB: dict[str, int] = {
    "small": 480,
    "medium": 1500,
    "large-v3": 3060,
    "large-v3-turbo": 1620,
}

DEFAULT_MODEL = "small"


def bundled_dir() -> Path:
    """`server/models/` inside the .dxt (or the repo root for dev installs)."""
    return Path(__file__).resolve().parent.parent / "models"


def bundled_path(short_name: str) -> Path:
    return bundled_dir() / f"whisper-{short_name}"


def is_bundled(short_name: str) -> bool:
    p = bundled_path(short_name)
    return p.is_dir() and any(p.iterdir())


def resolve(name: str) -> str:
    """Turn a user-facing name into something mlx-whisper accepts.

    Resolution order, first hit wins:
      1. Anything containing `/` is assumed to be an HF repo id and
         passed through (escape hatch for power users).
      2. The bundled directory (`server/models/<short>/`) — what the
         shipped .dxt drops on disk at build time.
      3. The HuggingFace hub cache (`~/.cache/huggingface/hub/...`) —
         where `prewarm_model` (and any prior `transcribe_video`
         download) deposits models.
      4. Last resort: the `org/repo` string itself, which signals to
         downstream callers (mlx-whisper / `prewarm_model`) that a
         download is required.
    """
    if "/" in name:
        return name
    repo = SHORT_TO_HF.get(name)
    if not repo:
        raise ValueError(
            f"unknown model '{name}'. Choices: {list(SHORT_TO_HF)} "
            f"(or pass a 'org/repo' HF id directly)"
        )
    bp = bundled_path(name)
    if bp.is_dir() and any(bp.iterdir()):
        return str(bp.resolve())
    cached = hf_cache_snapshot_path(repo)
    if cached:
        return cached
    return repo


def is_cached(short_name: str) -> bool:
    """True if either bundled or already present in the HF hub cache."""
    if is_bundled(short_name):
        return True
    repo = SHORT_TO_HF.get(short_name)
    return _hf_cache_has(repo) if repo else False


def _hf_cache_root() -> Path:
    return Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface"))


def _hf_cache_has(repo_id: str | None) -> bool:
    if not repo_id:
        return False
    root = _hf_cache_root()
    if not root.is_dir():
        return False
    safe = repo_id.replace("/", "--")
    return bool(list(root.rglob(f"models--{safe}")))


def hf_cache_snapshot_path(repo_id: str | None) -> str | None:
    """Locate a usable on-disk snapshot for `repo_id` in the HF hub cache.

    HF stores models at `~/.cache/huggingface/hub/models--ORG--REPO/
    snapshots/<commit>/`. A snapshot dir with at least one file is the
    canonical "model is downloaded" state. Returns the absolute path of
    the first usable snapshot, else None.
    """
    if not repo_id:
        return None
    root = _hf_cache_root()
    if not root.is_dir():
        return None
    safe = repo_id.replace("/", "--")
    for d in root.rglob(f"models--{safe}"):
        snapshots = d / "snapshots"
        if snapshots.is_dir():
            for snap in snapshots.iterdir():
                if snap.is_dir() and any(snap.iterdir()):
                    return str(snap.resolve())
    return None


def _hf_cache_size_mb(repo_id: str | None) -> int | None:
    if not repo_id:
        return None
    root = _hf_cache_root()
    if not root.is_dir():
        return None
    safe = repo_id.replace("/", "--")
    total = 0
    for d in root.rglob(f"models--{safe}"):
        for f in d.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return round(total / 1024 / 1024) if total else None


def _hf_cache_last_used(repo_id: str | None) -> float | None:
    if not repo_id:
        return None
    root = _hf_cache_root()
    if not root.is_dir():
        return None
    safe = repo_id.replace("/", "--")
    mtimes = [f.stat().st_mtime for d in root.rglob(f"models--{safe}")
              for f in d.rglob("*") if f.is_file()]
    return max(mtimes) if mtimes else None


def inventory() -> list[dict]:
    out: list[dict] = []
    for short, repo in SHORT_TO_HF.items():
        bundled = is_bundled(short)
        size_mb = _hf_cache_size_mb(repo) if not bundled else _du_mb(bundled_path(short))
        out.append({
            "name": short,
            "hf_repo": repo,
            "default": short == DEFAULT_MODEL,
            "bundled": bundled,
            "cached": bundled or _hf_cache_has(repo),
            "size_mb": size_mb if size_mb is not None else APPROX_SIZE_MB.get(short),
            "last_used_unix": _hf_cache_last_used(repo) if not bundled else None,
        })
    return out


def _du_mb(path: Path) -> int:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / 1024 / 1024)
