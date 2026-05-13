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

echo "==> Clean $BUILD"
rm -rf "$BUILD" "$ARTIFACT"
mkdir -p "$BUILD/server/bin" "$BUILD/server/lib" "$BUILD/wheels"

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
python3 -m pip download \
  --dest "$BUILD/wheels" \
  "${PY_PLATFORMS[@]}" "${PY_FLAGS[@]}" \
  'pydantic>=2.7' 'Pillow>=10.3' 'mcp>=1.0' \
  'mlx-whisper>=0.4' 'ffmpeg-python>=0.2.0' \
  >/dev/null

echo "==> Install wheels into server/lib"
python3 -m pip install \
  --target "$BUILD/server/lib" --no-deps --no-index \
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

echo "==> Validate manifest"
npx --yes @anthropic-ai/dxt@latest validate "$BUILD/manifest.json"

echo "==> Pack roughcut.dxt"
npx --yes @anthropic-ai/dxt@latest pack "$BUILD" "$ARTIFACT"

echo
echo "Built: $ARTIFACT"
ls -lh "$ARTIFACT"
