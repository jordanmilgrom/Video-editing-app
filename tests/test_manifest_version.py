"""Regression test: dxt/manifest.json `version` must be 3-component semver.

v0.6.5.1 shipped with a 4-component version ("0.6.5.1") which Anthropic's
`can_install` API rejected silently — Claude Desktop then surfaced the
rejection as a generic "Claude will return soon" service-unavailable
screen instead of a useful local error, blocking install for Jordan.

v0.5.0..v0.6.5 all shipped 3-component versions and installed cleanly.
This test enforces that invariant so we never regress the field shape
without immediately noticing in CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from packaging.version import Version

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "dxt" / "manifest.json"
SEMVER_THREE_PART = re.compile(r"^\d+\.\d+\.\d+$")


def test_manifest_version_parses_as_pep440() -> None:
    """packaging.version.Version must accept the manifest version string.

    PEP 440 is a superset of semver; if this rejects, the version is
    malformed entirely.
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    Version(manifest["version"])  # raises InvalidVersion on bad input


def test_manifest_version_is_three_numeric_components() -> None:
    """Anthropic's can_install API expects exactly MAJOR.MINOR.PATCH.

    Anything else (a 4th .N segment, a pre-release suffix, a build
    metadata tag) causes can_install to reject the manifest and Claude
    Desktop to show a generic service-unavailable error instead of
    installing the .dxt. Keep it boring: 3 ints, 2 dots, nothing else.
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    v = manifest["version"]
    assert SEMVER_THREE_PART.match(v), (
        f"manifest version {v!r} is not MAJOR.MINOR.PATCH; "
        f"Anthropic's can_install API will reject it"
    )


def test_manifest_version_matches_pyproject() -> None:
    """The two version sources should never drift — same .dxt, same wheel."""
    pyproject = (MANIFEST_PATH.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert m is not None, "pyproject.toml has no version line"
    pyproject_version = m.group(1)
    manifest_version = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["version"]
    assert pyproject_version == manifest_version, (
        f"pyproject.toml says {pyproject_version!r} but "
        f"dxt/manifest.json says {manifest_version!r}"
    )
