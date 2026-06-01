#!/usr/bin/env bash
# Build roughcut.dxt — a Claude Desktop Extension bundling roughcut_mcp,
# all Python wheels for macOS arm64, and static ffmpeg/ffprobe binaries.
#
# Usage:   bash scripts/build-dxt.sh
# Output:  ./roughcut.dxt
#
# Requirements on the build host: python3 (with pip), node + npm (npx),
# and ~1 GB free disk. The build host does NOT need to be macOS — wheels
# are downloaded with explicit platform tags for darwin/arm64.

set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
BUILD="$PROJECT_ROOT/dxt-build"
ARTIFACT="$PROJECT_ROOT/roughcut.dxt"

PY_PLATFORMS=(
  --platform macosx_11_0_arm64
  --platform macosx_12_0_arm64
  --platform macosx_13_0_arm64
  --platform macosx_14_0_arm64
  --platform macosx_15_0_arm64
)
PY_FLAGS=(
  --python-version 3.11
  --implementation cp
  --abi cp311
  --only-binary=:all:
)

# Detect --break-system-packages support. pip >= 23.0 (Feb 2023) enforces
# PEP 668 on macOS and uses this flag to bypass it; older pip (e.g. Xcode
# CLT's bundled 21.2.4) predates both the protection and the bypass flag,
# so passing it errors out. Conditionally include.
EXTRA_PIP_FLAGS=()
if python3 -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
  EXTRA_PIP_FLAGS+=(--break-system-packages)
fi

echo "==> Clean $BUILD"
rm -rf "$BUILD" "$ARTIFACT"
mkdir -p "$BUILD/server/bin" "$BUILD/server/lib" "$BUILD/wheels"

echo "==> Fetch portable Python 3.11 (python-build-standalone, macOS arm64)"
# Bundled so Claude Desktop never has to resolve `python3` against the GUI PATH
# (which is /usr/bin/python3 = 3.9 on Sequoia, regardless of what the user has
# in their shell). Pinned version; bump in lockstep across builds.
PBS_TAG="20260510"
PBS_FILE="cpython-3.11.15+${PBS_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_FILE}"
curl -fsSL -o "$BUILD/pbs.tar.gz" "$PBS_URL"
tar -xzf "$BUILD/pbs.tar.gz" -C "$BUILD/server"
rm "$BUILD/pbs.tar.gz"
test -x "$BUILD/server/python/bin/python3.11"

echo "==> Fetch ffmpeg + ffprobe (macOS arm64) from npm"
(
  cd "$BUILD"
  npm pack --silent @ffmpeg-installer/darwin-arm64 @ffprobe-installer/darwin-arm64 >/dev/null
  tar -xzf ffmpeg-installer-darwin-arm64-*.tgz package/ffmpeg
  mv package/ffmpeg server/bin/ffmpeg
  rm -rf package
  tar -xzf ffprobe-installer-darwin-arm64-*.tgz package/ffprobe
  mv package/ffprobe server/bin/ffprobe
  rm -rf package ffmpeg-installer-darwin-arm64-*.tgz ffprobe-installer-darwin-arm64-*.tgz
  chmod +x server/bin/ffmpeg server/bin/ffprobe
)

echo "==> Download Python wheels (darwin/arm64, cp311)"
# mlx-metal must be listed explicitly. mlx's METADATA marks it as
# `Requires-Dist: mlx-metal==X; platform_system == "Darwin"`, but pip's
# marker evaluation here uses the BUILD HOST's platform (Linux in CI),
# not the --platform target, so the marker reads as False and mlx-metal
# gets silently dropped. Without it, mlx/core.cpython-311-darwin.so loads
# at import time and dyld fails to find @rpath/libmlx.dylib.
python3 -m pip download \
  --dest "$BUILD/wheels" \
  "${PY_PLATFORMS[@]}" "${PY_FLAGS[@]}" \
  'pydantic>=2.7' 'Pillow>=10.3' 'mcp>=1.0' \
  'mlx-whisper>=0.4' 'mlx-metal' 'ffmpeg-python>=0.2.0' \
  >/dev/null

echo "==> Install wheels into server/lib"
python3 -m pip install \
  --target "$BUILD/server/lib" --no-deps --no-index "${EXTRA_PIP_FLAGS[@]}" \
  --find-links "$BUILD/wheels" \
  "${PY_PLATFORMS[@]}" "${PY_FLAGS[@]}" \
  "$BUILD"/wheels/*.whl \
  >/dev/null

echo "==> Prune torch (only mlx_whisper/torch_whisper.py uses it; we don't ship the HF→MLX converter)"
rm -rf "$BUILD/server/lib/torch" "$BUILD"/server/lib/torch-*.dist-info \
       "$BUILD/server/lib/sympy" "$BUILD"/server/lib/sympy-*.dist-info \
       "$BUILD/server/lib/networkx" "$BUILD"/server/lib/networkx-*.dist-info \
       "$BUILD/server/lib/jinja2" "$BUILD"/server/lib/jinja2-*.dist-info \
       "$BUILD/server/lib/mpmath" "$BUILD"/server/lib/mpmath-*.dist-info \
       "$BUILD/server/lib/mlx_whisper/torch_whisper.py"
rm -rf "$BUILD/wheels"

echo "==> Stage roughcut source + manifest + entry"
cp -R "$PROJECT_ROOT/roughcut_core" "$PROJECT_ROOT/roughcut_mcp" "$BUILD/server/"
find "$BUILD/server" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
cp "$PROJECT_ROOT/dxt/manifest.json" "$BUILD/manifest.json"
cp "$PROJECT_ROOT/dxt/main.py" "$BUILD/server/main.py"

echo "==> Bundle whisper-small MLX model (~480 MB; offline first-run transcribe)"
# Set ROUGHCUT_SKIP_MODEL_BUNDLE=1 to skip when working offline. Production
# CI builds must NOT set this — the bundled model is the whole point of v0.6.1.
WHISPER_MODEL_DIR="$BUILD/server/models/whisper-small"
if [ -n "${ROUGHCUT_SKIP_MODEL_BUNDLE:-}" ]; then
  echo "    (skipped: ROUGHCUT_SKIP_MODEL_BUNDLE=$ROUGHCUT_SKIP_MODEL_BUNDLE)"
  mkdir -p "$WHISPER_MODEL_DIR"
  echo "ROUGHCUT_SKIP_MODEL_BUNDLE was set; rebuild without it for a shippable .dxt." \
    > "$WHISPER_MODEL_DIR/.placeholder"
else
  python3 -m pip install --quiet "${EXTRA_PIP_FLAGS[@]}" 'huggingface_hub>=0.20' >/dev/null
  mkdir -p "$WHISPER_MODEL_DIR"
  python3 - <<PY
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="mlx-community/whisper-small-mlx",
    local_dir="$WHISPER_MODEL_DIR",
)
PY
  test -f "$WHISPER_MODEL_DIR/config.json" || {
    echo "Bundling failed: $WHISPER_MODEL_DIR/config.json missing" >&2
    exit 1
  }
fi

echo "==> Validate manifest"
npx --yes @anthropic-ai/dxt@latest validate "$BUILD/manifest.json"

echo "==> Pack roughcut.dxt"
npx --yes @anthropic-ai/dxt@latest pack "$BUILD" "$ARTIFACT"

echo
echo "Built: $ARTIFACT"
ls -lh "$ARTIFACT"
