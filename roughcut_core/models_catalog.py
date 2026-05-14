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

# Approximate on-disk sizes in MB, for surfacing to the user before a
# download. Real values differ slightly across mlx-community quantizations.
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

    - Anything containing `/` is assumed to be an HF repo id and passed
      through (escape hatch for power users).
    - Otherwise the short name maps to its mlx-community repo. If we've
      bundled that model, returns the absolute local path so transcription
      is fully offline.
    """
    if "/" in name:
        return name
    repo = SHORT_TO_HF.get(name)
    if not repo:
        raise ValueError(
            f"unknown model '{name}'. Choices: {list(SHORT_TO_HF)} "
            f"(or pass a 'org/repo' HF id directly)"
        )
    if is_bundled(name):
        return str(bundled_path(name).resolve())
    return repo


def is_cached(short_name: str) -> bool:
    """True if either bundled or already present in the HF hub cache.

    `transcribe_video` uses this to decide whether to auto-prewarm
    in-flight before running the model.
    """
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
    """Whatever `get_system_status` wants to surface about local models."""
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
