"""Multicam mode: parallel-camera podcast support.

Three concerns live here:
  1. Group clips that were rolling at the same time by cross-correlating
     their audio waveforms. No timecode, no jam sync required — works on
     consumer cameras + lav recorders that started independently.
  2. Label each transcript segment with the speaker by comparing mic
     RMS across the synced group. Each speaker's lav is loud when that
     speaker talks; bleed is comparatively quiet.
  3. Pick the camera angle per segment + sprinkle reaction shots, so
     the downstream FCPXML emitter can lay them out on V1.

mlx-whisper / torch / pyannote are NOT imported here. The whole pipeline
is numpy + scipy + ffmpeg. That keeps the .dxt size manageable and the
runtime dependency surface deterministic.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from scipy.signal import correlate

from roughcut_core.models import AngleSelection, MulticamGroup, SpeakerLabel, Transcript

SYNC_SAMPLE_RATE = 4_000   # plenty for envelope-level cross-correlation
RMS_SAMPLE_RATE = 16_000   # finer-grained for per-segment speaker decisions


def detect_groups(folder: Path) -> list[MulticamGroup]:
    """Find clips that were rolling simultaneously, grouped by audio sync.

    Strategy: extract a 4 kHz mono envelope per clip, cross-correlate
    every pair, accept pairs whose peak exceeds a noise-floor heuristic.
    Connected components of accepted pairs become groups; offsets within
    each group are propagated from the chosen anchor.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    clips = sorted(_iter_media(folder))
    if not clips:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        envelopes = [_extract_envelope(c, Path(tmp), SYNC_SAMPLE_RATE) for c in clips]
        pairs = _pairwise_offsets(envelopes, SYNC_SAMPLE_RATE)
    return _build_groups(clips, pairs)


def diarize_by_mic_dominance(
    transcript: Transcript,
    group: MulticamGroup,
    speaker_labels: list[str] | None = None,
) -> list[SpeakerLabel]:
    """Per-segment: speaker = clip with the loudest mic over that interval.

    Assumes each clip in `group` has the speaker's own lav prominent
    (the typical 2+ host podcast setup). Bleed is fine; whoever is
    actually talking has the loudest mic.

    `transcript` is timestamped against ONE of the clips in the group —
    typically the first. The transcript's `source_path` determines the
    reference clock; offsets in `group.offsets_sec` are added/subtracted
    to align with each clip's local time.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    if not group.clip_paths:
        return []
    speaker_names = _resolve_speaker_names(group, speaker_labels)
    ref_idx = _find_reference_idx(transcript, group)
    with tempfile.TemporaryDirectory() as tmp:
        wavs = [_extract_mono(c, Path(tmp), RMS_SAMPLE_RATE) for c in group.clip_paths]
        samples = [_read_wav_mono(w) for w in wavs]
    out: list[SpeakerLabel] = []
    for seg_idx, seg in enumerate(transcript.segments):
        rms_per_clip: list[float] = []
        for clip_idx, audio in enumerate(samples):
            # transcript clock → reference clip clock (ref offset is 0 by construction)
            # → target clip clock: subtract target's offset.
            local_start = seg.start - (group.offsets_sec[clip_idx] - group.offsets_sec[ref_idx])
            local_end = seg.end - (group.offsets_sec[clip_idx] - group.offsets_sec[ref_idx])
            rms_per_clip.append(_rms_slice(audio, RMS_SAMPLE_RATE, local_start, local_end))
        speaker_idx = int(np.argmax(rms_per_clip))
        out.append(SpeakerLabel(
            segment_idx=seg_idx,
            speaker_idx=speaker_idx,
            speaker_label=speaker_names[speaker_idx],
            rms_per_clip=[float(r) for r in rms_per_clip],
        ))
    return out


def pick_angles(
    transcript: Transcript,
    diarization: list[SpeakerLabel],
    group: MulticamGroup,
    reaction_interval_sec: float = 30.0,
    reaction_hold_sec: float = 2.0,
) -> list[AngleSelection]:
    """Camera per segment + occasional reaction shots.

    Primary angle = speaker's own camera. Every `reaction_interval_sec`
    we cut to the OTHER speaker's camera for `reaction_hold_sec` so the
    rough cut isn't a static talking-head loop. If a speaker holds the
    floor for less than the reaction interval, no reaction shot is
    inserted in that stretch.

    Timeline coordinates are 1:1 with the transcript's clock.
    """
    if not transcript.segments or not diarization:
        return []
    selections: list[AngleSelection] = []
    last_reaction_at = -reaction_interval_sec
    ref_idx = _find_reference_idx(transcript, group)
    for seg, label in zip(transcript.segments, diarization):
        speaker_clip_idx = label.speaker_idx
        if seg.start - last_reaction_at >= reaction_interval_sec and len(group.clip_paths) > 1:
            other_idx = (speaker_clip_idx + 1) % len(group.clip_paths)
            hold = min(reaction_hold_sec, seg.end - seg.start)
            selections.append(_make_angle(seg.start, seg.start + hold, other_idx, group, ref_idx, "reaction"))
            selections.append(_make_angle(seg.start + hold, seg.end, speaker_clip_idx, group, ref_idx, "primary"))
            last_reaction_at = seg.start
        else:
            selections.append(_make_angle(seg.start, seg.end, speaker_clip_idx, group, ref_idx, "primary"))
    return _merge_consecutive(selections)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_MEDIA_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".wav", ".mp3", ".m4a", ".aac", ".flac"}


def _iter_media(folder: Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(folder)
    return [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _MEDIA_EXTS]


def _extract_envelope(media: Path, tmpdir: Path, sr: int) -> np.ndarray:
    """Decode to mono PCM at `sr` Hz, return an abs-amplitude envelope."""
    wav = tmpdir / f"{media.stem}.{sr}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(media),
         "-vn", "-ac", "1", "-ar", str(sr), "-f", "wav", str(wav)],
        check=True,
    )
    audio = _read_wav_mono(wav)
    return np.abs(audio).astype(np.float32)


def _extract_mono(media: Path, tmpdir: Path, sr: int) -> Path:
    wav = tmpdir / f"{media.stem}.{sr}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(media),
         "-vn", "-ac", "1", "-ar", str(sr), "-f", "wav", str(wav)],
        check=True,
    )
    return wav


def _read_wav_mono(path: Path) -> np.ndarray:
    """Return float32 samples in [-1, 1]. WAV must be 16-bit signed mono PCM."""
    with wave.open(str(path), "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    return (np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0)


def _pairwise_offsets(envs: list[np.ndarray], sr: int) -> list[tuple[int, int, float, float]]:
    """For each (i, j) pair: best offset (seconds) and a confidence in [0, 1]."""
    pairs: list[tuple[int, int, float, float]] = []
    for i in range(len(envs)):
        for j in range(i + 1, len(envs)):
            a, b = envs[i], envs[j]
            xc = correlate(a - a.mean(), b - b.mean(), mode="full", method="fft")
            lag = int(np.argmax(xc)) - (len(b) - 1)
            peak = float(np.max(xc))
            denom = float(np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
            confidence = max(0.0, min(1.0, peak / denom))
            pairs.append((i, j, lag / sr, confidence))
    return pairs


def _build_groups(clips: list[Path], pairs: list[tuple[int, int, float, float]]) -> list[MulticamGroup]:
    """Connected-components grouping by accepted pairs; propagate offsets."""
    accepted = [(i, j, off, conf) for i, j, off, conf in pairs if conf >= 0.05]
    n = len(clips)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _, _ in accepted:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    groups: dict[int, list[int]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)

    out: list[MulticamGroup] = []
    for gi, members in enumerate(sorted(groups.values(), key=lambda m: -len(m))):
        if len(members) < 2:
            continue
        offsets = _propagate_offsets(members, accepted)
        anchor = members[int(np.argmin(offsets))]
        anchor_offset = offsets[members.index(anchor)]
        normalized = [off - anchor_offset for off in offsets]
        conf_avg = float(np.mean([c for i, j, _, c in accepted if i in members and j in members])) if accepted else 0.0
        out.append(MulticamGroup(
            group_id=f"g{gi}",
            clip_paths=[clips[m] for m in members],
            offsets_sec=normalized,
            confidence=conf_avg,
        ))
    return out


def _propagate_offsets(members: list[int], accepted: list[tuple[int, int, float, float]]) -> list[float]:
    """BFS from members[0] propagating pair offsets."""
    pos = {members[0]: 0.0}
    todo = [members[0]]
    while todo:
        cur = todo.pop()
        for i, j, off, _ in accepted:
            if i == cur and j in members and j not in pos:
                pos[j] = pos[cur] + off
                todo.append(j)
            elif j == cur and i in members and i not in pos:
                pos[i] = pos[cur] - off
                todo.append(i)
    return [pos.get(m, 0.0) for m in members]


def _rms_slice(audio: np.ndarray, sr: int, start_sec: float, end_sec: float) -> float:
    s = max(0, int(start_sec * sr))
    e = min(len(audio), int(end_sec * sr))
    if e <= s:
        return 0.0
    seg = audio[s:e]
    return float(np.sqrt(np.mean(seg * seg)))


def _resolve_speaker_names(group: MulticamGroup, labels: list[str] | None) -> list[str]:
    if labels and len(labels) == len(group.clip_paths):
        return list(labels)
    return [f"Speaker {i + 1}" for i in range(len(group.clip_paths))]


def _find_reference_idx(transcript: Transcript, group: MulticamGroup) -> int:
    """Index in group.clip_paths whose source matches the transcript."""
    target = Path(transcript.source_path).resolve()
    for i, p in enumerate(group.clip_paths):
        if Path(p).resolve() == target:
            return i
    return 0


def _make_angle(
    t_in: float, t_out: float, clip_idx: int, group: MulticamGroup, ref_idx: int, reason: str
) -> AngleSelection:
    # Convert reference-clock times to the target clip's local times.
    delta = group.offsets_sec[clip_idx] - group.offsets_sec[ref_idx]
    return AngleSelection(
        timeline_in_sec=max(0.0, t_in),
        timeline_out_sec=max(t_in, t_out),
        clip_path=group.clip_paths[clip_idx],
        clip_in_sec=max(0.0, t_in - delta),
        clip_out_sec=max(t_in - delta, t_out - delta),
        reason=reason,
    )


def _merge_consecutive(selections: list[AngleSelection]) -> list[AngleSelection]:
    """Collapse back-to-back angles on the same clip (after reaction-shot splits)."""
    if not selections:
        return []
    out = [selections[0]]
    for s in selections[1:]:
        prev = out[-1]
        if (prev.clip_path == s.clip_path
                and prev.reason == s.reason
                and abs(prev.timeline_out_sec - s.timeline_in_sec) < 1e-6):
            out[-1] = AngleSelection(
                timeline_in_sec=prev.timeline_in_sec,
                timeline_out_sec=s.timeline_out_sec,
                clip_path=prev.clip_path,
                clip_in_sec=prev.clip_in_sec,
                clip_out_sec=s.clip_out_sec,
                reason=prev.reason,
            )
        else:
            out.append(s)
    return out
