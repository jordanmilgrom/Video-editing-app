"""Persist large MCP payloads to disk so tool results stay under Desktop's 1 MB cap.

Claude Desktop hard-rejects any tool result larger than 1 MB. Real
roughcut payloads (full transcripts, per-line script alignments, large
clip inventories, multicam offsets) routinely exceed that on real
footage. Every affected tool writes its full payload to JSON in the
cache dir and returns just the path plus a small summary.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def stable_key(*parts: Any) -> str:
    """sha256 over `|`-joined parts. Deterministic across runs and OSes."""
    payload = "|".join(str(p) for p in parts).encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def write_json(cache_dir: Path, subdir: str, key: str, data: Any) -> Path:
    """Dump JSON to `<cache_dir>/<subdir>/<key>.json`; return the path."""
    out_dir = cache_dir / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{key}.json"
    out_path.write_text(
        json.dumps(data, default=_default, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _default(o: Any) -> Any:
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"{type(o).__name__} is not JSON serializable")
