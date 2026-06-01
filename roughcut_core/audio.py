"""Waveform-based silence + breath detection.

`find_silences` in `documentary.py` (v0.7.0) finds gaps between Whisper
*segments* — coarse-grained. Whisper happily merges 5 seconds of speech
that contains a 1-second mid-segment pause into one segment, so the
transcript-side detector misses internal silences. v0.9.0 closes that
gap by actually examining the audio.

Pipeline:

  ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 -f s16le -
        \\--------- mono PCM at 16 kHz, raw little-endian s16 -------/
  → numpy reshape into 50ms frames
  → per-frame RMS → dBFS
  → threshold + run-length encode into spans

For a 10-minute clip at 16 kHz that's ~19.2 MB of raw PCM and ~12000
50ms frames; numpy crunches the entire envelope in milliseconds. The
expensive part is the ffmpeg decode (1-3s per minute of source).

The decoded envelope is exposed as `decode_audio_envelope` so callers
that want both `find_audio_silences` and `detect_breaths` (e.g. the
fused `tighten_take`) can pay the decode cost once and reuse.

Thresholds:
  default_silence_db   = -40   typical clean room tone floor
  default_breath_db    = -30   below speech but above silence
  default_min_silence  = 0.3s  shorter than this is just inter-syllabic pauses
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

from roughcut_core.models import Transcript

SAMPLE_RATE = 16_000     # Hz — plenty for speech-band silence detection
FRAME_MS = 50            # ms per envelope frame
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 800 samples / 50ms

DEFAULT_SILENCE_DB = -40.0
DEFAULT_BREATH_DB = -30.0
DEFAULT_MIN_SILENCE_SEC = 0.3
DEFAULT_MIN_BREATH_SEC = 0.15
DEFAULT_MAX_BREATH_SEC = 0.8


def decode_audio_envelope(video_path: Path) -> np.ndarray:
    """Decode `video_path` audio to a per-frame dBFS envelope.

    Returns a 1-D float32 array of length `n_frames` where
    `n_frames * FRAME_MS / 1000` ≈ duration_sec. Each value is the RMS
    of that 50ms frame expressed in dBFS (0 = full scale, -inf = silent
    — actually we floor at -90 dBFS to keep things bounded).
    """
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    raw = _decode_pcm(video_path)
    samples = np.frombuffer(raw, dtype=np.int16)
    n_frames = len(samples) // SAMPLES_PER_FRAME
    if n_frames == 0:
        return np.zeros(0, dtype=np.float32)
    trimmed = samples[: n_frames * SAMPLES_PER_FRAME]
    frames = trimmed.astype(np.float32).reshape(n_frames, SAMPLES_PER_FRAME)
    rms = np.sqrt((frames * frames).mean(axis=1))
    # Convert to dBFS. Floor RMS at 1 to avoid log(0); 1/32768 = -90 dBFS.
    dbfs = 20.0 * np.log10(np.maximum(rms, 1.0) / 32768.0)
    return dbfs.astype(np.float32)


def find_audio_silences(
    video_path: Path,
    min_silence_sec: float = DEFAULT_MIN_SILENCE_SEC,
    threshold_db: float = DEFAULT_SILENCE_DB,
    start_sec: float | None = None,
    end_sec: float | None = None,
    envelope: np.ndarray | None = None,
) -> dict:
    """Return spans of waveform silence (RMS < threshold_db).

    This is the audio-side complement to `documentary.find_silences`,
    which is transcript-based. Use both — silences this catches are
    typically MID-segment pauses Whisper merged into one segment.

    `envelope` is the optional pre-decoded dBFS envelope from
    `decode_audio_envelope`. Pass it when calling multiple analyses on
    the same clip (saves 1-3s per minute of source).
    """
    if envelope is None:
        envelope = decode_audio_envelope(video_path)

    interval = FRAME_MS / 1000.0
    duration_sec = round(len(envelope) * interval, 3)
    lo = 0.0 if start_sec is None else float(start_sec)
    hi = duration_sec if end_sec is None else float(end_sec)
    lo_frame = max(0, int(lo / interval))
    hi_frame = min(len(envelope), int(np.ceil(hi / interval)))

    min_frames = max(1, int(round(min_silence_sec / interval)))
    silent_mask = (envelope[lo_frame:hi_frame] < threshold_db)
    silences = _runs_to_spans(silent_mask, lo_frame, interval, min_frames)

    return {
        "video_path": str(video_path),
        "range": [round(lo, 3), round(hi, 3)],
        "threshold_db": threshold_db,
        "min_silence_sec": min_silence_sec,
        "duration_sec": duration_sec,
        "silences": silences,
        "total_silence_sec": round(
            sum(s["duration_sec"] for s in silences), 3
        ),
        "silence_pct": (
            round(100.0 * sum(s["duration_sec"] for s in silences) / (hi - lo), 1)
            if hi > lo else 0.0
        ),
    }


def detect_breaths(
    video_path: Path,
    transcript_path: Path,
    start_sec: float | None = None,
    end_sec: float | None = None,
    min_breath_sec: float = DEFAULT_MIN_BREATH_SEC,
    max_breath_sec: float = DEFAULT_MAX_BREATH_SEC,
    silence_floor_db: float = -55.0,
    speech_floor_db: float = DEFAULT_BREATH_DB,
    envelope: np.ndarray | None = None,
) -> dict:
    """Find breaths / lip smacks between transcribed words.

    A "breath" candidate is a span sitting BETWEEN two consecutive
    transcribed Word entries where:
      - duration is in [min_breath_sec, max_breath_sec]
      - mean RMS is below `speech_floor_db` (not actively speech)
      - mean RMS is above `silence_floor_db` (not pure silence — there
        IS energy, just not speech-energy)

    Without the lower bound we'd just flag silences (which
    `find_audio_silences` already handles); the lower bound focuses on
    spans with body — exhales, inhales, lip parting, tongue clicks.

    Skips Whisper segments without per-word stamps.
    """
    if envelope is None:
        envelope = decode_audio_envelope(video_path)
    t = Transcript.model_validate_json(
        Path(transcript_path).read_text(encoding="utf-8")
    )
    interval = FRAME_MS / 1000.0
    duration_sec = round(len(envelope) * interval, 3)
    lo = 0.0 if start_sec is None else float(start_sec)
    hi = duration_sec if end_sec is None else float(end_sec)

    breaths: list[dict] = []
    for seg in t.segments:
        if seg.end <= lo or seg.start >= hi or not seg.words:
            continue
        for i in range(len(seg.words) - 1):
            w_a = seg.words[i]
            w_b = seg.words[i + 1]
            gap_start = w_a.end
            gap_end = w_b.start
            gap = gap_end - gap_start
            if not (min_breath_sec <= gap <= max_breath_sec):
                continue
            if gap_end <= lo or gap_start >= hi:
                continue
            f_start = max(0, int(gap_start / interval))
            f_end = min(len(envelope), int(np.ceil(gap_end / interval)))
            if f_end <= f_start:
                continue
            mean_db = float(envelope[f_start:f_end].mean())
            if silence_floor_db < mean_db < speech_floor_db:
                breaths.append({
                    "start_sec": round(gap_start, 3),
                    "end_sec": round(gap_end, 3),
                    "duration_sec": round(gap, 3),
                    "mean_db": round(mean_db, 2),
                    "between_words": f"{w_a.text} / {w_b.text}",
                })

    return {
        "video_path": str(video_path),
        "transcript_path": str(transcript_path),
        "range": [round(lo, 3), round(hi, 3)],
        "min_breath_sec": min_breath_sec,
        "max_breath_sec": max_breath_sec,
        "silence_floor_db": silence_floor_db,
        "speech_floor_db": speech_floor_db,
        "breaths": breaths,
        "total_breath_sec": round(
            sum(b["duration_sec"] for b in breaths), 3
        ),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _decode_pcm(video_path: Path) -> bytes:
    """ffmpeg → mono 16 kHz 16-bit little-endian PCM stream."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-f", "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True)
    return proc.stdout


def _runs_to_spans(
    mask: np.ndarray, frame_offset: int, interval: float, min_frames: int,
) -> list[dict]:
    """Run-length-encode a boolean mask into [{start_sec, end_sec, duration_sec}].

    Only emits runs at least `min_frames` long. Times are absolute
    seconds (i.e. accounting for `frame_offset`, the index of the
    mask's first frame within the full envelope).
    """
    if mask.size == 0:
        return []
    # Find rising / falling edges.
    padded = np.concatenate(([False], mask, [False]))
    edges = np.diff(padded.astype(np.int8))
    starts = np.where(edges == 1)[0]
    ends = np.where(edges == -1)[0]

    spans: list[dict] = []
    for s, e in zip(starts, ends):
        if (e - s) < min_frames:
            continue
        start_sec = (frame_offset + s) * interval
        end_sec = (frame_offset + e) * interval
        spans.append({
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
            "duration_sec": round(end_sec - start_sec, 3),
        })
    return spans
