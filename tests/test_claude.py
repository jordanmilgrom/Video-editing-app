"""Tests for the Claude wrapper. The actual SDK call is not exercised."""

from __future__ import annotations

from roughcut import claude


def test_load_prompt_substitutes_vars() -> None:
    text = claude.load_prompt("pick_best_take", cluster="A=1\nB=2")
    assert "A=1" in text and "B=2" in text
    assert "{{cluster}}" not in text


def test_load_prompt_leaves_unused_placeholders_alone() -> None:
    text = claude.load_prompt("cluster_takes")
    assert "{{segments}}" in text
