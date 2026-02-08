#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-.}"

# Resolve to absolute path
TARGET_DIR=$(cd "$TARGET_DIR" && pwd)

echo "Uninstalling review-loop from: $TARGET_DIR"

# Remove bin/review-loop.sh
if [[ -f "$TARGET_DIR/bin/review-loop.sh" ]]; then
  rm "$TARGET_DIR/bin/review-loop.sh"
  echo "Removed bin/review-loop.sh"
  # Remove bin/ if empty
  rmdir "$TARGET_DIR/bin" 2>/dev/null && echo "Removed empty bin/" || true
fi

# Remove only prompt files installed by this tool
for _pfile in codex-review.prompt.md claude-fix.prompt.md; do
  if [[ -f "$TARGET_DIR/prompts/active/$_pfile" ]]; then
    rm "$TARGET_DIR/prompts/active/$_pfile"
    echo "Removed prompts/active/$_pfile"
  fi
done
# Remove directories only if empty
rmdir "$TARGET_DIR/prompts/active" 2>/dev/null && echo "Removed empty prompts/active/" || true
rmdir "$TARGET_DIR/prompts" 2>/dev/null && echo "Removed empty prompts/" || true

# Remove .reviewlooprc.example
if [[ -f "$TARGET_DIR/.reviewlooprc.example" ]]; then
  rm "$TARGET_DIR/.reviewlooprc.example"
  echo "Removed .reviewlooprc.example"
fi

# Remove .ai-review-logs/ entry from .gitignore only if it was added by install.sh
# The installer writes a "# AI review logs" comment immediately before the entry.
# If the entry exists without that comment, it was user-managed and must be preserved.
GITIGNORE="$TARGET_DIR/.gitignore"
if [[ -f "$GITIGNORE" ]] && grep -qxF '# AI review logs' "$GITIGNORE"; then
  TMP_GITIGNORE=$(mktemp)
  # Remove only the installer-owned comment + entry pair
  awk '
    /^# AI review logs$/ { marker=1; next }
    marker && /^\.ai-review-logs\/$/ { marker=0; next }
    { if (marker) print "# AI review logs"; marker=0; print }
    END { if (marker) print "# AI review logs" }
  ' "$GITIGNORE" > "$TMP_GITIGNORE"
  # Remove trailing blank lines
  sed -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$TMP_GITIGNORE" > "$GITIGNORE"
  rm -f "$TMP_GITIGNORE"
  echo "Removed .ai-review-logs/ from .gitignore"
fi

echo "Done. review-loop has been uninstalled."
