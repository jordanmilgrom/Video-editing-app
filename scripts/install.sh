#!/usr/bin/env bash
# roughcut bootstrap installer for macOS (Apple Silicon).
#
# What it does, in order:
#   1. Verifies you're on macOS arm64 with Claude Desktop installed.
#   2. Installs Homebrew if missing (official one-liner).
#   3. Installs python@3.11 and node via brew if missing.
#   4. Clones jordanmilgrom/Video-editing-app to ~/Video-editing-app
#      (or `git pull origin main` if it's already there).
#   5. Builds roughcut.dxt (downloads ~200 MB of wheels + ffmpeg).
#   6. Opens roughcut.dxt so Claude Desktop shows its install dialog.
#
# Idempotent — re-running just refreshes the source and rebuilds.
#
# How to run:
#   curl -fsSLO https://raw.githubusercontent.com/jordanmilgrom/Video-editing-app/main/scripts/install.sh
#   bash install.sh

set -e

REPO_URL="https://github.com/jordanmilgrom/Video-editing-app.git"
REPO_DIR="$HOME/Video-editing-app"

step() { printf '\n==> %s\n' "$*"; }

# ----- Sanity checks ---------------------------------------------------------
if [ "$(uname -s)" != "Darwin" ]; then
  echo "This installer only runs on macOS." >&2
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "roughcut requires Apple Silicon (M1/M2/M3/M4). Your Mac is $(uname -m)." >&2
  exit 1
fi
if [ ! -d "/Applications/Claude.app" ]; then
  echo "Claude Desktop is not installed." >&2
  echo "Download it from https://claude.ai/download, sign in, then re-run this script." >&2
  exit 1
fi

# ----- Homebrew --------------------------------------------------------------
step "Checking for Homebrew"
if command -v brew >/dev/null 2>&1; then
  echo "Homebrew already installed: $(brew --version | head -1)"
else
  echo "Installing Homebrew (Terminal will ask for your Mac password)…"
  # Run interactively so sudo can prompt over the tty. With NONINTERACTIVE=1
  # the installer bails with "Need sudo access" on a clean Mac because no
  # auth path is available.
  /bin/bash -c \
    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
# Make brew + its installed binaries available in THIS shell.
if [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# ----- Python 3.11 -----------------------------------------------------------
step "Checking Python 3.11"
if brew list python@3.11 >/dev/null 2>&1; then
  echo "python@3.11 already installed."
else
  echo "Installing python@3.11…"
  brew install python@3.11
fi
# Put python@3.11's `python3` ahead of macOS's system python.
export PATH="$(brew --prefix python@3.11)/libexec/bin:$PATH"

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if [ "$PY_VER" != "3.11" ]; then
  echo "Expected python3 to resolve to 3.11, got $PY_VER. Aborting." >&2
  exit 1
fi
echo "Using $(python3 --version) at $(command -v python3)"

# ----- Node.js (for the dxt CLI via npx) -------------------------------------
step "Checking Node.js"
if brew list node >/dev/null 2>&1 || command -v node >/dev/null 2>&1; then
  echo "node already available: $(node --version 2>/dev/null || echo unknown)"
else
  echo "Installing node…"
  brew install node
fi

# ----- Fetch source ----------------------------------------------------------
step "Fetching roughcut source ($REPO_DIR)"
if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR"
  echo "Repo exists at $REPO_DIR; resetting to latest origin/main…"
  # Hard-reset rather than `git pull origin main` so divergent local history
  # (from prior install.sh runs that committed local edits, or an aborted
  # rebase) doesn't block the upgrade. The installer is not a place for
  # preserving local mods — anyone hacking on the source uses a separate
  # checkout per the "For developers" section of the README.
  git fetch origin main
  git reset --hard origin/main
elif [ -d "$REPO_DIR" ]; then
  echo "$REPO_DIR exists but is not a git checkout. Move it aside and re-run." >&2
  exit 1
else
  echo "Cloning $REPO_URL → $REPO_DIR…"
  git clone "$REPO_URL" "$REPO_DIR"
  cd "$REPO_DIR"
fi

# ----- Build the .dxt --------------------------------------------------------
step "Building roughcut.dxt (~5 min, downloads ~200 MB)"
bash scripts/build-dxt.sh

# ----- Hand off to Claude Desktop --------------------------------------------
step "Opening roughcut.dxt in Claude Desktop"
if [ ! -f "$REPO_DIR/roughcut.dxt" ]; then
  echo "Build did not produce $REPO_DIR/roughcut.dxt. See errors above." >&2
  exit 1
fi
open "$REPO_DIR/roughcut.dxt"

cat <<EOF

================================================================
Done. Claude Desktop should now show an install dialog.

  • Click "Install" in the dialog.
  • Fill in (or skip) the three folder fields: interview footage,
    b-roll, and script path. You can change these later.
  • After install, quit Claude Desktop (Cmd-Q) and reopen it so
    the extension loads.

Built artifact: $REPO_DIR/roughcut.dxt
================================================================
EOF
