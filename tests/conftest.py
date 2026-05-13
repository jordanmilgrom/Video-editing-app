"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


requires_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg/ffprobe not installed on PATH"
)


@pytest.fixture
def tiny_wav(tmp_path: Path) -> Path:
    """A 1-second silent 16 kHz mono WAV — small, deterministic test fixture."""
    out = tmp_path / "tiny.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "1", str(out)],
        check=True,
    )
    return out
