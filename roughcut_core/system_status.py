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
    return {
        "python": _python_info(),
        "ffmpeg": _binary_check("ffmpeg", ["-version"]),
        "ffprobe": _binary_check("ffprobe", ["-version"]),
        "mlx_whisper_importable": _mlx_whisper_check(),
        "whisper_model_cached": _whisper_model_check(),
        "cache_dir": _cache_dir_check(cache_dir),
        "disk_free_gb": _disk_free_gb(cache_dir),
        "env": {
            "ROUGHCUT_CACHE_DIR": os.environ.get("ROUGHCUT_CACHE_DIR"),
            "ROUGHCUT_INTERVIEW_DIR": os.environ.get("ROUGHCUT_INTERVIEW_DIR"),
            "ROUGHCUT_BROLL_DIR": os.environ.get("ROUGHCUT_BROLL_DIR"),
            "ROUGHCUT_SCRIPT_PATH": os.environ.get("ROUGHCUT_SCRIPT_PATH"),
        },
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


def _whisper_model_check() -> dict:
    """The model gets fetched on first transcribe — flag whether it's already on disk."""
    home = Path.home() / ".cache" / "huggingface"
    if not home.is_dir():
        return {"ok": False, "error": "HuggingFace cache dir missing",
                "hint": "First transcribe will download the model (~1.5 GB)."}
    matches = list(home.rglob("*whisper-large*"))
    return {"ok": bool(matches), "matches": [str(m) for m in matches[:3]]}


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
