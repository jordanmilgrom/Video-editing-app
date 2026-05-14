"""Tests for the whisper model resolution helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from roughcut_core import models_catalog


def test_resolve_passes_through_explicit_hf_repo() -> None:
    out = models_catalog.resolve("some-org/some-model")
    assert out == "some-org/some-model"


def test_resolve_unknown_short_name_raises() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        models_catalog.resolve("xxl")


def test_resolve_short_name_falls_back_to_hf_repo_when_not_bundled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(models_catalog, "bundled_dir", lambda: tmp_path / "no-such-dir")
    assert models_catalog.resolve("small") == "mlx-community/whisper-small-mlx"


def test_resolve_short_name_returns_bundled_path_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_bundle = tmp_path / "models"
    (fake_bundle / "whisper-small").mkdir(parents=True)
    (fake_bundle / "whisper-small" / "config.json").write_text("{}")
    monkeypatch.setattr(models_catalog, "bundled_dir", lambda: fake_bundle)
    out = models_catalog.resolve("small")
    assert out.endswith("whisper-small")
    assert Path(out).is_dir()


def test_inventory_marks_bundled_and_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_bundle = tmp_path / "models"
    (fake_bundle / "whisper-small").mkdir(parents=True)
    (fake_bundle / "whisper-small" / "weights.npz").write_text("x")
    monkeypatch.setattr(models_catalog, "bundled_dir", lambda: fake_bundle)
    # Pretend the HF cache is empty.
    monkeypatch.setattr(models_catalog, "_hf_cache_has", lambda repo: False)
    items = models_catalog.inventory()
    by_name = {item["name"]: item for item in items}
    assert by_name["small"]["bundled"] is True
    assert by_name["small"]["cached"] is True
    assert by_name["small"]["default"] is True
    assert by_name["large-v3"]["bundled"] is False
    assert by_name["large-v3"]["cached"] is False


def test_is_cached_returns_true_when_bundled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_bundle = tmp_path / "models"
    (fake_bundle / "whisper-medium").mkdir(parents=True)
    (fake_bundle / "whisper-medium" / "config.json").write_text("{}")
    monkeypatch.setattr(models_catalog, "bundled_dir", lambda: fake_bundle)
    assert models_catalog.is_cached("medium") is True
    assert models_catalog.is_cached("large-v3") is False
