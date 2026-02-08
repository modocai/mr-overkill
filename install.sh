#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
TARGET_DIR="${1:-.}"

# Resolve to absolute path
TARGET_DIR=$(cd "$TARGET_DIR" && pwd)

echo "Installing review-loop into: $TARGET_DIR"

# Copy bin/ and templates/
cp -r "$SCRIPT_DIR/bin" "$TARGET_DIR/"
cp -r "$SCRIPT_DIR/templates" "$TARGET_DIR/"
chmod +x "$TARGET_DIR/bin/review-loop.sh"

# Add .ai-review-logs/ to .gitignore if not already present
GITIGNORE="$TARGET_DIR/.gitignore"
if [[ -f "$GITIGNORE" ]]; then
  if ! grep -qF '.ai-review-logs/' "$GITIGNORE"; then
    echo "" >> "$GITIGNORE"
    echo "# AI review logs" >> "$GITIGNORE"
    echo ".ai-review-logs/" >> "$GITIGNORE"
    echo "Added .ai-review-logs/ to existing .gitignore"
  else
    echo ".ai-review-logs/ already in .gitignore"
  fi
else
  cat > "$GITIGNORE" <<'EOF'
# AI review logs
.ai-review-logs/
EOF
  echo "Created .gitignore with .ai-review-logs/"
fi

echo "Done. Run: bin/review-loop.sh -n 3"
