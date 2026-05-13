"""Tests for take detection / best-take pick. Claude is monkeypatched."""

from __future__ import annotations

from pathlib import Path

from roughcut import claude, takes
from roughcut.models import Segment, Transcript


def _seg(text: str, start: float, end: float) -> Segment:
    return Segment(text=text, start=start, end=end, words=[])


def _transcript(segments: list[Segment]) -> Transcript:
    return Transcript(
        source_path=Path("/tmp/x.wav"),
        source_hash="hash",
        duration=segments[-1].end if segments else 0.0,
        language="en",
        segments=segments,
    )


def test_split_variants_breaks_on_silence_gap() -> None:
    segs = [
        _seg("hello", 0.0, 1.0),
        _seg("world", 1.2, 2.0),
        _seg("again", 5.0, 6.0),  # gap = 3.0 > SILENCE_GAP
    ]
    variants = takes._split_variants(segs)
    assert [len(v) for v in variants] == [2, 1]
    assert variants[0][0].text == "hello"
    assert variants[1][0].text == "again"


def test_best_line_matches_with_threshold() -> None:
    lines = ["hello world", "goodbye my friend"]
    assert takes._best_line("hello world", lines) == 0
    assert takes._best_line("goodbye my friend", lines) == 1
    assert takes._best_line("completely unrelated", lines) is None


def test_detect_takes_with_script(monkeypatch) -> None:
    segs = [
        _seg("hello world", 0.0, 1.0),
        _seg("hello world", 5.0, 6.0),  # second read of line 0
        _seg("goodbye my friend", 10.0, 11.0),
    ]
    t = _transcript(segs)

    def fake_call(prompt_name, schema, *, tool_name=claude.DEFAULT_TOOL_NAME, **vars):
        assert prompt_name == "pick_best_take"
        assert tool_name == "select_take"
        return {"chosen_index": 1, "reason": "cleaner read"}

    monkeypatch.setattr(claude, "call", fake_call)
    out = takes.detect_takes(t, script_text="hello world\ngoodbye my friend")

    # line 0: 2 variants (one chosen) + line 1: 1 variant (chosen)
    assert len(out) == 3
    line0 = [tk for tk in out if tk.cluster_id == "c0"]
    assert len(line0) == 2
    chosen = [tk for tk in line0 if tk.chosen]
    assert len(chosen) == 1
    assert chosen[0].in_sec == 5.0
    assert chosen[0].reason == "cleaner read"

    line1 = [tk for tk in out if tk.cluster_id == "c1"]
    assert len(line1) == 1 and line1[0].chosen


def test_detect_takes_silence_mode(monkeypatch) -> None:
    segs = [
        _seg("a a a", 0.0, 1.0),
        _seg("a a a", 5.0, 6.0),
        _seg("b b b", 10.0, 11.0),
    ]
    t = _transcript(segs)
    calls = {"cluster": 0, "pick": 0}

    def fake_call(prompt_name, schema, *, tool_name=claude.DEFAULT_TOOL_NAME, **vars):
        if tool_name == "cluster_takes":
            calls["cluster"] += 1
            return {"clusters": [[0, 1], [2]]}
        if tool_name == "select_take":
            calls["pick"] += 1
            return {"chosen_index": 0, "reason": "first is cleanest"}
        raise AssertionError(f"unexpected tool {tool_name}")

    monkeypatch.setattr(claude, "call", fake_call)
    out = takes.detect_takes(t)

    assert calls == {"cluster": 1, "pick": 1}  # second cluster has 1 variant → no pick call
    chosen = [tk for tk in out if tk.chosen]
    assert len(chosen) == 2  # one per cluster
