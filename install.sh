#!/usr/bin/env bash
set -euo pipefail

# install.sh — Install mr-overkill and initialize a project.
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

# ── Step 1: Install Python package ──────────────────────────────────
if $DEV_MODE; then
  echo "Installing mr-overkill (editable, local checkout)..."
  SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
  if command -v uv &>/dev/null; then
    uv tool install -e "$SCRIPT_DIR"
  elif command -v pipx &>/dev/null; then
    pipx install -e "$SCRIPT_DIR"
  else
    pip install -e "$SCRIPT_DIR"
  fi
else
  echo "Installing mr-overkill..."
  if command -v uv &>/dev/null; then
    uv tool install mr-overkill
  elif command -v pipx &>/dev/null; then
    pipx install mr-overkill
  else
    pip install mr-overkill
  fi
fi

# ── Step 2: Initialize project ──────────────────────────────────────
mr-overkill init "$TARGET_DIR"
