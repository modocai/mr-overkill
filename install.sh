#!/usr/bin/env bash
set -euo pipefail

# install.sh — Install overkill and initialize a project.
#
# Usage:
#   ./install.sh [TARGET_DIR]     # install from PyPI + init
#   ./install.sh --dev [TARGET]   # editable install from local checkout + init

TARGET_DIR="${1:-.}"
DEV_MODE=false

if [[ "${1:-}" == "--dev" ]]; then
  DEV_MODE=true
  TARGET_DIR="${2:-.}"
fi

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "Error: target directory '$TARGET_DIR' does not exist." >&2
  exit 1
fi

# ── Step 1: Install Python package ──────────────────────────────────
if $DEV_MODE; then
  echo "Installing overkill (editable, local checkout)..."
  SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
  if command -v uv &>/dev/null; then
    uv tool install --reinstall -e "$SCRIPT_DIR"
  elif command -v pipx &>/dev/null; then
    pipx install --force -e "$SCRIPT_DIR"
  else
    pip install -e "$SCRIPT_DIR"
  fi
else
  echo "Installing overkill..."
  if command -v uv &>/dev/null; then
    uv tool install --reinstall overkill
  elif command -v pipx &>/dev/null; then
    pipx install --force overkill
  else
    pip install overkill
  fi
fi

# ── Step 2: Initialize project ──────────────────────────────────────
if ! command -v overkill &>/dev/null; then
  echo "Warning: 'overkill' not found on PATH after installation." >&2
  echo "You may need to add ~/.local/bin to your PATH, then run:" >&2
  echo "  overkill init $TARGET_DIR" >&2
  exit 1
fi

overkill init "$TARGET_DIR"
