#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
TARGET_DIR="${1:-.}"

# Read version from review-loop.sh
VERSION=$(grep -m1 '^VERSION=' "$SCRIPT_DIR/bin/review-loop.sh" | cut -d'"' -f2)

# Ensure target directory exists and resolve to absolute path
if [[ ! -d "$TARGET_DIR" ]]; then
  echo "Error: target directory '$TARGET_DIR' does not exist."
  exit 1
fi
TARGET_DIR=$(cd "$TARGET_DIR" && pwd)

echo "Installing review-loop v${VERSION} into: $TARGET_DIR"

# Copy bin/ and active prompts
cp -r "$SCRIPT_DIR/bin" "$TARGET_DIR/"
mkdir -p "$TARGET_DIR/prompts/active"
cp "$SCRIPT_DIR"/prompts/active/* "$TARGET_DIR/prompts/active/"
chmod +x "$TARGET_DIR/bin/review-loop.sh"

# Copy .reviewlooprc.example
if [[ -f "$SCRIPT_DIR/.reviewlooprc.example" ]]; then
  cp "$SCRIPT_DIR/.reviewlooprc.example" "$TARGET_DIR/"
  echo "Copied .reviewlooprc.example"
fi

# Add .ai-review-logs/ to .gitignore if not already present
GITIGNORE="$TARGET_DIR/.gitignore"
if [[ -f "$GITIGNORE" ]]; then
  if ! grep -qF '.ai-review-logs/' "$GITIGNORE"; then
    echo "" >> "$GITIGNORE"
    echo "# AI review logs (added by review-loop installer)" >> "$GITIGNORE"
    echo ".ai-review-logs/" >> "$GITIGNORE"
    echo "Added .ai-review-logs/ to existing .gitignore"
  else
    echo ".ai-review-logs/ already in .gitignore"
  fi
else
  cat > "$GITIGNORE" <<'EOF'
# AI review logs (added by review-loop installer)
.ai-review-logs/
EOF
  echo "Created .gitignore with .ai-review-logs/"
fi

echo "Done. Run: bin/review-loop.sh -n 3"
