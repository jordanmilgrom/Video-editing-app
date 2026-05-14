"""Preflight diagnostics: surface broken bundles before the user tries a job.

Every entry returns a dict the MCP layer flattens into the tool result.
Failures are descriptive, not just `ok=false`, so the agent can read
them and tell the user what's actually wrong.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def collect(cache_dir: Path) -> dict:
    from roughcut_core import models_catalog
    return {
        "python": _python_info(),
        "ffmpeg": _binary_check("ffmpeg", ["-version"]),
        "ffprobe": _binary_check("ffprobe", ["-version"]),
        "mlx_whisper_importable": _mlx_whisper_check(),
        "models": _models_block(models_catalog.inventory()),
        "cache_dir": _cache_dir_check(cache_dir),
        "disk_free_gb": _disk_free_gb(cache_dir),
        "env": {
            "ROUGHCUT_CACHE_DIR": os.environ.get("ROUGHCUT_CACHE_DIR"),
            "ROUGHCUT_INTERVIEW_DIR": os.environ.get("ROUGHCUT_INTERVIEW_DIR"),
            "ROUGHCUT_BROLL_DIR": os.environ.get("ROUGHCUT_BROLL_DIR"),
            "ROUGHCUT_SCRIPT_PATH": os.environ.get("ROUGHCUT_SCRIPT_PATH"),
        },
    }


def _models_block(items: list[dict]) -> dict:
    """Roll the models inventory into the {ok, ...} shape `collect` uses.

    `ok=True` iff at least one model is on disk — otherwise the first
    transcribe will block on a multi-minute download.
    """
    any_cached = any(m["cached"] for m in items)
    return {
        "ok": any_cached,
        "available": items,
        "hint": None if any_cached else (
            "No whisper models cached yet. The first `transcribe_video` "
            "will download one (~480 MB for `small`, ~3 GB for `large-v3`). "
            "Call `prewarm_model('small')` to pre-fetch in the background."
        ),
    }


def _python_info() -> dict:
    return {
        "ok": True,
        "executable": sys.executable,
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }


def _binary_check(name: str, probe: list[str]) -> dict:
    path = shutil.which(name)
    if path is None:
        return {"ok": False, "error": f"{name} not on PATH",
                "hint": "Bundled binary missing. Reinstall the .dxt."}
    try:
        out = subprocess.run([path, *probe], check=True, capture_output=True, text=True, timeout=5)
        version = (out.stdout.splitlines() or out.stderr.splitlines() or [""])[0]
        return {"ok": True, "path": path, "version": version}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": path, "error": f"{type(e).__name__}: {e}"}


def _mlx_whisper_check() -> dict:
    """Catches the libmlx.dylib regression class without doing a real transcribe."""
    try:
        import mlx.core  # noqa: F401  (loads core.cpython-311-darwin.so → libmlx.dylib)
        import mlx_whisper  # noqa: F401
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        hint = None
        if "libmlx" in msg.lower() or "@rpath" in msg.lower():
            hint = ("libmlx.dylib didn't load. The .dxt build is missing the "
                    "mlx-metal wheel. Rebuild with bash scripts/build-dxt.sh.")
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "hint": hint}


def _cache_dir_check(cache_dir: Path) -> dict:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"ok": True, "path": str(cache_dir)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": str(cache_dir),
                "error": f"{type(e).__name__}: {e}",
                "hint": "Cache dir isn't writable. Check folder permissions."}


def _disk_free_gb(cache_dir: Path) -> dict:
    try:
        target = cache_dir if cache_dir.exists() else cache_dir.parent
        usage = shutil.disk_usage(target)
        free_gb = round(usage.free / 1024 ** 3, 2)
        warn = free_gb < 5.0
        return {"ok": not warn, "free_gb": free_gb,
                "hint": "Less than 5 GB free — transcriptions can fill cache quickly." if warn else None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
