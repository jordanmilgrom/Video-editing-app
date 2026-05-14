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


# ----- v0.6.2: HF-cache fallback chain --------------------------------------


def _make_hf_cache(root: Path, repo_id: str) -> Path:
    """Lay out a fake HF hub cache snapshot for `repo_id`. Returns the snapshot dir."""
    safe = repo_id.replace("/", "--")
    snap = root / "hub" / f"models--{safe}" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}")
    (snap / "weights.safetensors").write_text("x")
    return snap


def test_hf_cache_snapshot_path_finds_downloaded_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    snap = _make_hf_cache(tmp_path, "mlx-community/whisper-large-v3-mlx")
    out = models_catalog.hf_cache_snapshot_path("mlx-community/whisper-large-v3-mlx")
    assert out == str(snap.resolve())


def test_hf_cache_snapshot_path_returns_none_when_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert models_catalog.hf_cache_snapshot_path("mlx-community/whisper-large-v3-mlx") is None


def test_resolve_falls_back_to_hf_cache_when_not_bundled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bug fix: prewarmed models on disk must resolve to a local path,
    not the repo id."""
    monkeypatch.setattr(models_catalog, "bundled_dir", lambda: tmp_path / "no-bundle")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    snap = _make_hf_cache(tmp_path / "hf", "mlx-community/whisper-large-v3-mlx")
    out = models_catalog.resolve("large-v3")
    assert out == str(snap.resolve())
    assert "/" in out and out.startswith("/")  # absolute path, not "org/repo"


def test_resolve_returns_repo_id_only_when_no_bundle_and_no_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(models_catalog, "bundled_dir", lambda: tmp_path / "no-bundle")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf"))
    out = models_catalog.resolve("large-v3")
    assert out == "mlx-community/whisper-large-v3-mlx"


def test_resolve_prefers_bundled_over_hf_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bundled is always source of truth — even if HF cache also has it."""
    fake_bundle = tmp_path / "models"
    (fake_bundle / "whisper-small").mkdir(parents=True)
    (fake_bundle / "whisper-small" / "config.json").write_text("{}")
    monkeypatch.setattr(models_catalog, "bundled_dir", lambda: fake_bundle)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    _make_hf_cache(tmp_path / "hf", "mlx-community/whisper-small-mlx")
    out = models_catalog.resolve("small")
    assert out.endswith("models/whisper-small")
