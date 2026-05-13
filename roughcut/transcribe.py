"""Interview transcription with caching.

Pipeline:
1. Compute a cheap cache key from absolute path + size + mtime.
2. If a cached `Transcript` exists, return it.
3. Otherwise extract 16 kHz mono WAV with ffmpeg, run mlx-whisper with
   word-level timestamps, persist the Transcript atomically, return it.

mlx-whisper is Apple-Silicon-only. It is imported lazily inside
`_run_whisper` so this module is importable in CI/Linux for tests; tests
swap `_run_whisper` via monkeypatch.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from roughcut.models import Segment, Transcript, Word

WHISPER_MODEL = "mlx-community/whisper-large-v3-mlx"
AUDIO_SAMPLE_RATE = 16_000
SUPPORTED_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".wav", ".mp3", ".m4a"}


def cache_key(path: Path) -> str:
    """sha256 over absolute path + size + mtime_ns. Fast, cheap, deterministic."""
    st = path.stat()
    payload = f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()
    return hashlib.sha256(payload).hexdigest()


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / "transcripts" / f"{key}.json"


def _atomic_write_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def extract_audio(media_path: Path, out_wav: Path) -> None:
    """Extract a 16 kHz mono PCM WAV using ffmpeg on PATH."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(media_path),
        "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE),
        "-f", "wav", str(out_wav),
    ]
    subprocess.run(cmd, check=True)


def _probe_duration(media_path: Path) -> float:
    """Use ffprobe (ships with ffmpeg) to read media duration in seconds."""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found on PATH")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(media_path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip() or 0.0)


def _run_whisper(wav_path: Path) -> dict:
    """Run mlx-whisper. Imported lazily; tests monkeypatch this function."""
    import mlx_whisper  # type: ignore[import-not-found]

    return mlx_whisper.transcribe(
        str(wav_path),
        path_or_hf_repo=WHISPER_MODEL,
        word_timestamps=True,
    )


def _parse_whisper_result(raw: dict, source_path: Path, source_hash: str, duration: float) -> Transcript:
    segments: list[Segment] = []
    for seg in raw.get("segments", []):
        words = [
            Word(text=w["word"].strip(), start=float(w["start"]), end=float(w["end"]))
            for w in seg.get("words", [])
            if w.get("word") is not None
        ]
        segments.append(
            Segment(
                text=str(seg.get("text", "")).strip(),
                start=float(seg["start"]),
                end=float(seg["end"]),
                words=words,
            )
        )
    return Transcript(
        source_path=source_path,
        source_hash=source_hash,
        duration=duration,
        language=raw.get("language"),
        segments=segments,
    )


def transcribe(media_path: Path, cache_dir: Path) -> Transcript:
    """Transcribe a single media file, hitting cache when available."""
    media_path = Path(media_path)
    key = cache_key(media_path)
    cache_file = _cache_path(cache_dir, key)
    if cache_file.exists():
        return Transcript.model_validate_json(cache_file.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as tmpdir:
        wav = Path(tmpdir) / "audio.wav"
        extract_audio(media_path, wav)
        raw = _run_whisper(wav)

    duration = _probe_duration(media_path)
    transcript = _parse_whisper_result(raw, media_path, key, duration)
    _atomic_write_json(cache_file, transcript.model_dump_json())
    return transcript


def transcribe_folder(interview_dir: Path, cache_dir: Path) -> list[Transcript]:
    """Transcribe every supported media file in a folder, sorted by name."""
    files = sorted(
        p for p in Path(interview_dir).iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    )
    return [transcribe(f, cache_dir) for f in files]


__all__ = [
    "AUDIO_SAMPLE_RATE",
    "SUPPORTED_EXTS",
    "cache_key",
    "extract_audio",
    "transcribe",
    "transcribe_folder",
]
