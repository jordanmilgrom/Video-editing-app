# Building `roughcut.dxt`

```
bash scripts/build-dxt.sh
```

That's it. Output lands at `./roughcut.dxt` (~170 MB).

## What the script does

1. Wipes any previous `dxt-build/` tree.
2. Fetches a portable CPython 3.11 (`python-build-standalone`,
   `aarch64-apple-darwin`, `install_only_stripped` variant) from the
   `astral-sh/python-build-standalone` GitHub release and extracts it
   to `dxt-build/server/python/`. The manifest points `command` at
   this bundled interpreter, so Claude Desktop never has to resolve
   `python3` against the GUI PATH (which on Sequoia is `/usr/bin/python3`
   = 3.9 and would fail any 3.11 preflight check).
3. Fetches static `ffmpeg` + `ffprobe` (macOS arm64) from the
   `@ffmpeg-installer/darwin-arm64` and `@ffprobe-installer/darwin-arm64`
   npm packages.
4. `pip download`s Python wheels for `darwin / arm64 / cp311`, plus
   transitives, into `dxt-build/wheels/`.
5. `pip install --target dxt-build/server/lib --no-deps --no-index`
   so the bundled Python can find them via `PYTHONPATH`.
6. Prunes `torch`, `sympy`, `networkx`, `jinja2`, `mpmath`, and
   `mlx_whisper/torch_whisper.py` — those are only used by
   mlx-whisper's HF→MLX checkpoint converter, which we don't ship.
   Saves ~410 MB.
7. Copies `roughcut_core/`, `roughcut_mcp/`, `dxt/manifest.json`, and
   `dxt/main.py` into the staging tree.
8. Runs `npx @anthropic-ai/dxt validate` on the manifest, then
   `npx @anthropic-ai/dxt pack` to produce `roughcut.dxt`.

## Build-host requirements

- `python3` (any version with pip; the wheels are downloaded with
  explicit `cp311` ABI tags, so the build host's own Python version
  doesn't matter).
- `node` + `npm` (for `npx` and the `npm pack` step that pulls the
  ffmpeg binaries).
- ~1 GB free disk during the build.
- Network: pypi.org, registry.npmjs.org, github.com (for the
  python-build-standalone release tarball).

You do **not** need to be on macOS to build the `.dxt`. Wheels are
downloaded with `--platform macosx_*_arm64` and the ffmpeg binaries
come pre-built. The resulting `.dxt` only runs on macOS arm64, though.

## Shipping a new release

The `.dxt` is **not** committed to the repo (it's gitignored). Cut a
GitHub Release and upload `roughcut.dxt` as an asset:

```
gh release create v0.4.0 \
  --title "roughcut v0.4.0" \
  --notes "First .dxt release. macOS arm64 only." \
  roughcut.dxt
```

Bump `version` in `dxt/manifest.json` and `pyproject.toml` together.

## Layout reference

```
dxt/                       # source (committed)
  manifest.json            #   ← edit this
  main.py                  #   ← thin entry point

scripts/
  build-dxt.sh             # the one-line rebuild command

dxt-build/                 # generated, gitignored
  manifest.json            # copied from dxt/manifest.json
  server/
    main.py                # copied from dxt/main.py
    roughcut_core/         # copied from project root
    roughcut_mcp/          # copied from project root
    bin/
      ffmpeg               # macOS arm64 static binary
      ffprobe              # macOS arm64 static binary
    lib/                   # vendored Python wheels, installed flat

roughcut.dxt               # generated, gitignored — the artifact
```
