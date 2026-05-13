"""Tests for sentence → b-roll matching. Claude is monkeypatched."""

from __future__ import annotations

from pathlib import Path

from roughcut import claude, match
from roughcut.models import Clip, Take


def _take(text: str, in_sec: float, out_sec: float, chosen: bool = True) -> Take:
    return Take(
        source_path=Path("/tmp/x.wav"),
        source_hash="h",
        in_sec=in_sec,
        out_sec=out_sec,
        text=text,
        cluster_id="c0",
        chosen=chosen,
        reason="",
    )


def _clip(hash_: str, subject: str, duration: float = 5.0) -> Clip:
    return Clip(
        source_path=Path(f"/tmp/{hash_}.mp4"),
        source_hash=hash_,
        duration=duration,
        subject=subject,
        motion="static",
        mood="neutral",
        tags=[subject],
        suggested_in_sec=0.5,
        suggested_out_sec=duration - 0.5,
        description=f"shot of {subject}",
    )


def test_split_sentences_handles_punctuation() -> None:
    out = match._split_sentences("Hello world. This is fine! Right?")
    assert out == ["Hello world.", "This is fine!", "Right?"]


def test_time_sentences_proportional_by_length() -> None:
    sents = ["aaa", "bbbbbb"]  # 3 + 6 chars; second is 2x longer
    out = match._time_sentences(sents, 10.0, 19.0)  # 9-sec window
    assert out[0][0] == 10.0
    # first sentence: 9 * 3/9 = 3.0
    assert round(out[0][1] - out[0][0], 3) == 3.0
    # second sentence: 9 * 6/9 = 6.0
    assert round(out[1][1] - out[1][0], 3) == 6.0
    assert out[1][1] == 19.0


def test_match_emits_offset_relative_to_aroll(monkeypatch) -> None:
    takes = [
        _take("First sentence. Second sentence.", in_sec=10.0, out_sec=20.0),  # 10s
        _take("Third one.", in_sec=30.0, out_sec=35.0),  # 5s, starts at A-roll t=10
        _take("rejected.", in_sec=0.0, out_sec=1.0, chosen=False),  # ignored
    ]
    clips = {"clipA": _clip("clipA", "ocean"), "clipB": _clip("clipB", "city")}

    calls: list[str] = []

    def fake_call(prompt_name, schema, *, tool_name=claude.DEFAULT_TOOL_NAME, **vars):
        sentence = vars["sentence"]
        calls.append(sentence)
        # Always pick clipA for first sentence, clipB for second, none after.
        if "First" in sentence:
            return {"picks": [{"clip_hash": "clipA", "clip_in_sec": 0.5, "clip_out_sec": 2.5, "reason": "ocean"}]}
        if "Second" in sentence:
            return {"picks": [{"clip_hash": "clipB", "clip_in_sec": 0.0, "clip_out_sec": 1.5}]}
        return {"picks": []}

    monkeypatch.setattr(claude, "call", fake_call)
    matches = match.match(takes, clips)

    assert len(calls) == 3  # 2 sentences from take 0, 1 from take 1
    assert len(matches) == 2

    # First sentence inside take 0: a-roll offset starts at 0.
    first = matches[0]
    assert first.aroll_offset_sec == 0.0
    assert first.clip_hash == "clipA"

    # Second sentence's offset: first sentence consumes ~half of take 0 (10s).
    second = matches[1]
    assert 4.0 < second.aroll_offset_sec < 6.0
    assert second.clip_hash == "clipB"


def test_match_drops_picks_with_unknown_clip(monkeypatch) -> None:
    takes = [_take("Some line.", 0.0, 5.0)]
    clips = {"realA": _clip("realA", "ocean")}

    def fake_call(*a, **k):
        return {"picks": [
            {"clip_hash": "ghost", "clip_in_sec": 0.0, "clip_out_sec": 1.0},
            {"clip_hash": "realA", "clip_in_sec": 0.0, "clip_out_sec": 1.5},
        ]}

    monkeypatch.setattr(claude, "call", fake_call)
    matches = match.match(takes, clips)
    assert len(matches) == 1
    assert matches[0].clip_hash == "realA"
