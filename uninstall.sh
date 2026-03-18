#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-.}"

# Ensure target directory exists and resolve to absolute path
if [[ ! -d "$TARGET_DIR" ]]; then
  echo "Error: target directory '$TARGET_DIR' does not exist."
  exit 1
fi
TARGET_DIR=$(cd "$TARGET_DIR" && pwd)

## Remove a marker+entry block from .gitignore, clean up if empty.
## Usage: remove_gitignore_block <marker_line> <entry_regex> <label>
remove_gitignore_block() {
  local marker="$1" entry_re="$2" label="$3"
  [[ -f "$GITIGNORE" ]] && grep -qxF "$marker" "$GITIGNORE" || return 0
  local tmp
  tmp=$(mktemp)
  awk -v marker="$marker" -v entry="$entry_re" '
    $0 == marker { skip=1; next }
    skip && /^[[:space:]]*$/ { next }
    skip && $0 ~ entry { skip=0; next }
    { skip=0; print }
  ' "$GITIGNORE" > "$tmp"
  awk '{ lines[NR]=$0; if(NF) last=NR } END { for(i=1;i<=last;i++) print lines[i] }' "$tmp" > "$GITIGNORE"
  rm -f "$tmp"
  echo "Removed $label from .gitignore"
  if [[ ! -s "$GITIGNORE" ]]; then
    rm "$GITIGNORE"
    echo "Removed empty .gitignore"
  fi
}

echo "Uninstalling overkill from: $TARGET_DIR"

# Remove .overkill/ (current layout)
if [[ -d "$TARGET_DIR/.overkill" ]]; then
  _install_dir="$TARGET_DIR/.overkill"

  if [[ -f "$_install_dir/.install-manifest" ]]; then
    while IFS= read -r _entry; do
      [[ -n "$_entry" ]] || continue
      case "$_entry" in
        ../*|*/../*|*/..) echo "Skipping unsafe manifest entry: $_entry" >&2; continue ;;
      esac
      if [[ -f "$_install_dir/$_entry" ]]; then
        rm "$_install_dir/$_entry"
        echo "Removed .overkill/$_entry"
      fi
    done < "$_install_dir/.install-manifest"
    rm "$_install_dir/.install-manifest"
    echo "Removed .install-manifest"
    find "$_install_dir" -mindepth 1 -type d -empty -delete 2>/dev/null || true
  fi

  if [[ -d "$_install_dir/logs" ]]; then
    rm -rf "$_install_dir/logs"
    echo "Removed .overkill/logs/"
  fi
  rmdir "$_install_dir" 2>/dev/null && echo "Removed empty .overkill/" || true
fi

# Remove legacy .review-loop/ (pre-migration installs)
if [[ -d "$TARGET_DIR/.review-loop" ]]; then
  _install_dir="$TARGET_DIR/.review-loop"

  if [[ -f "$_install_dir/.install-manifest" ]]; then
    # Manifest-driven removal
    while IFS= read -r _entry; do
      [[ -n "$_entry" ]] || continue
      # Reject path traversal
      case "$_entry" in
        ../*|*/../*|*/..) echo "Skipping unsafe manifest entry: $_entry" >&2; continue ;;
      esac
      if [[ -f "$_install_dir/$_entry" ]]; then
        rm "$_install_dir/$_entry"
        echo "Removed .review-loop/$_entry"
      fi
    done < "$_install_dir/.install-manifest"
    rm "$_install_dir/.install-manifest"
    echo "Removed .install-manifest"
    # Clean up empty directories left behind
    find "$_install_dir" -mindepth 1 -type d -empty -delete 2>/dev/null || true
  else
    # Legacy fallback: hardcoded file list (pre-manifest installs)
    for _bfile in review-loop.sh refactor-suggest.sh lib/common.sh lib/check-claude-limit.sh lib/check-codex-limit.sh lib/retry.sh lib/self-review.sh; do
      if [[ -f "$_install_dir/bin/$_bfile" ]]; then
        rm "$_install_dir/bin/$_bfile"
        echo "Removed .review-loop/bin/$_bfile"
      fi
    done
    rmdir "$_install_dir/bin/lib" 2>/dev/null && echo "Removed empty .review-loop/bin/lib/" || true
    rmdir "$_install_dir/bin" 2>/dev/null && echo "Removed empty .review-loop/bin/" || true
    for _pfile in codex-review.prompt.md claude-fix.prompt.md claude-fix-execute.prompt.md claude-self-review.prompt.md \
      codex-refactor-micro.prompt.md codex-refactor-module.prompt.md codex-refactor-layer.prompt.md codex-refactor-full.prompt.md \
      claude-refactor-fix.prompt.md claude-refactor-fix-execute.prompt.md; do
      if [[ -f "$_install_dir/prompts/active/$_pfile" ]]; then
        rm "$_install_dir/prompts/active/$_pfile"
        echo "Removed .review-loop/prompts/active/$_pfile"
      fi
    done
    rmdir "$_install_dir/prompts/active" 2>/dev/null && echo "Removed empty .review-loop/prompts/active/" || true
    rmdir "$_install_dir/prompts" 2>/dev/null && echo "Removed empty .review-loop/prompts/" || true
    for _rc in .reviewlooprc.example .refactorsuggestrc.example; do
      if [[ -f "$_install_dir/$_rc" ]]; then
        rm "$_install_dir/$_rc"
        echo "Removed .review-loop/$_rc"
      fi
    done
  fi

  # logs/ (runtime artifacts)
  if [[ -d "$_install_dir/logs" ]]; then
    rm -rf "$_install_dir/logs"
    echo "Removed .review-loop/logs/"
  fi
  # Remove .review-loop/ only if empty (preserves user-added files)
  rmdir "$_install_dir" 2>/dev/null && echo "Removed empty .review-loop/" || true
fi

# Clean up .gitignore entries
GITIGNORE="$TARGET_DIR/.gitignore"
remove_gitignore_block "# overkill (added by overkill init)" '^\\.overkill/$' "overkill entries"
remove_gitignore_block "# review-loop (added by installer)" '^\\.review-loop/$' "review-loop entries"
remove_gitignore_block "# review-loop (added by overkill init)" '^\\.review-loop/$' "review-loop entries"
remove_gitignore_block "# AI review logs (added by review-loop installer)" '^\\.ai-review-logs/$' ".ai-review-logs/ entry"

echo "Done. overkill has been uninstalled."
