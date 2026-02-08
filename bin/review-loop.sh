#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
TEMPLATES_DIR="$SCRIPT_DIR/../templates"

# ── Defaults ──────────────────────────────────────────────────────────
TARGET_BRANCH="develop"
MAX_LOOP=""
DRY_RUN=false

# ── Usage ─────────────────────────────────────────────────────────────
usage() {
  cat <<'EOF'
Usage: review-loop.sh [OPTIONS]

Options:
  -t, --target <branch>    Target branch to diff against (default: develop)
  -n, --max-loop <N>       Maximum review-fix iterations (required)
  --dry-run                Run review only, do not fix
  -h, --help               Show this help message

Flow:
  1. Codex reviews diff (target...current)
  2. Claude fixes all issues (P0-P3)
  3. Auto-commit & push fixes to update PR
  4. Post review/fix summary as PR comment
  5. Repeat until clean or max iterations

Examples:
  review-loop.sh -t main -n 3          # diff against main, max 3 loops
  review-loop.sh -n 5                  # diff against develop, max 5 loops
  review-loop.sh -n 1 --dry-run        # single review, no fixes
EOF
  exit "${1:-0}"
}

# ── Argument parsing ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--target)
      if [[ $# -lt 2 ]]; then echo "Error: '$1' requires an argument."; usage 1; fi
      TARGET_BRANCH="$2"; shift 2 ;;
    -n|--max-loop)
      if [[ $# -lt 2 ]]; then echo "Error: '$1' requires an argument."; usage 1; fi
      MAX_LOOP="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=true; shift ;;
    -h|--help)    usage ;;
    *)            echo "Error: unknown option '$1'"; usage 1 ;;
  esac
done

if [[ -z "$MAX_LOOP" ]]; then
  echo "Error: -n / --max-loop is required."
  echo ""
  usage 1
fi

if ! [[ "$MAX_LOOP" =~ ^[1-9][0-9]*$ ]]; then
  echo "Error: --max-loop must be a positive integer, got '$MAX_LOOP'."
  exit 1
fi

# ── Prerequisite checks ──────────────────────────────────────────────
check_cmd() {
  if ! command -v "$1" &>/dev/null; then
    echo "Error: '$1' is not installed or not in PATH."
    exit 1
  fi
}

check_cmd git
check_cmd codex
check_cmd claude
check_cmd jq
check_cmd envsubst
check_cmd gh

if ! git rev-parse --is-inside-work-tree &>/dev/null; then
  echo "Error: not inside a git repository."
  exit 1
fi

if ! git rev-parse --verify "$TARGET_BRANCH" &>/dev/null; then
  echo "Error: target branch '$TARGET_BRANCH' does not exist."
  exit 1
fi

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
LOG_DIR=".ai-review-logs"
mkdir -p "$LOG_DIR"

export CURRENT_BRANCH TARGET_BRANCH

# Detect open PR for current branch (if any)
PR_NUMBER=$(gh pr view "$CURRENT_BRANCH" --json number -q .number 2>/dev/null || true)
if [[ -n "$PR_NUMBER" ]]; then
  echo "Detected PR #$PR_NUMBER for branch $CURRENT_BRANCH"
else
  echo "No open PR detected — PR comments will be skipped."
fi

echo "═══════════════════════════════════════════════════════"
echo " Review Loop: $CURRENT_BRANCH → $TARGET_BRANCH"
echo " Max iterations: $MAX_LOOP | Dry-run: $DRY_RUN"
echo "═══════════════════════════════════════════════════════"
echo ""

# ── Loop ──────────────────────────────────────────────────────────────
FINAL_STATUS="max_iterations_reached"

for (( i=1; i<=MAX_LOOP; i++ )); do
  echo "───────────────────────────────────────────────────────"
  echo " Iteration $i / $MAX_LOOP"
  echo "───────────────────────────────────────────────────────"

  export ITERATION="$i"

  # ── a. Check diff ─────────────────────────────────────────────────
  DIFF=$(git diff "$TARGET_BRANCH...$CURRENT_BRANCH")
  if [[ -z "$DIFF" ]]; then
    echo "No diff between $TARGET_BRANCH and $CURRENT_BRANCH. Nothing to review."
    FINAL_STATUS="no_diff"
    break
  fi

  # ── b. Codex review ──────────────────────────────────────────────
  echo "[$(date +%H:%M:%S)] Running Codex review..."
  REVIEW_FILE="$LOG_DIR/review-${i}.json"
  rm -f "$REVIEW_FILE"

  REVIEW_PROMPT=$(envsubst < "$TEMPLATES_DIR/codex-review.prompt.md")

  if ! codex exec \
    --sandbox read-only \
    -o "$REVIEW_FILE" \
    "$REVIEW_PROMPT" 2>&1; then
    echo "Error: Codex review failed (iteration $i). Skipping this iteration."
    FINAL_STATUS="codex_error"
    break
  fi

  # ── c. Extract JSON from response ────────────────────────────────
  REVIEW_JSON=""
  if [[ ! -f "$REVIEW_FILE" ]]; then
    echo "Warning: review output file not found ($REVIEW_FILE). Codex may have failed."
  elif jq empty "$REVIEW_FILE" 2>/dev/null; then
    # Direct jq parse
    REVIEW_JSON=$(cat "$REVIEW_FILE")
  else
    # Extract JSON from markdown fences or mixed text
    REVIEW_JSON=$(sed -n '/^```\(json\)\{0,1\}$/,/^```$/{ /^```/d; p; }' "$REVIEW_FILE")
    # Fallback: find first { ... } block
    if ! echo "$REVIEW_JSON" | jq empty 2>/dev/null; then
      REVIEW_JSON=$(perl -0777 -ne 'print $1 if /(\{.*\})/s' "$REVIEW_FILE" 2>/dev/null || true)
    fi
  fi

  if ! echo "$REVIEW_JSON" | jq empty 2>/dev/null; then
    echo "Warning: could not parse review output as JSON. Saving raw output."
    echo "  See $REVIEW_FILE for details."
    FINAL_STATUS="parse_error"
    break
  fi

  # ── d. Check findings ────────────────────────────────────────────
  FINDINGS_COUNT=$(echo "$REVIEW_JSON" | jq '.findings | length')
  OVERALL=$(echo "$REVIEW_JSON" | jq -r '.overall_correctness')

  echo "  Findings: $FINDINGS_COUNT | Overall: $OVERALL"

  if [[ "$FINDINGS_COUNT" -eq 0 ]] && [[ "$OVERALL" == "patch is correct" ]]; then
    echo "  All clear — no issues found."
    # Post all-clear comment to PR
    if [[ -n "$PR_NUMBER" ]]; then
      gh pr comment "$PR_NUMBER" --body "$(cat <<EOF
### AI Review — Iteration $i ✅

No issues found. Patch is correct.
EOF
)"
      echo "  PR comment posted."
    fi
    FINAL_STATUS="all_clear"
    break
  fi

  # ── e. Dry-run check ─────────────────────────────────────────────
  if [[ "$DRY_RUN" == true ]]; then
    echo "  Dry-run mode — skipping fixes."
    FINAL_STATUS="dry_run"
    break
  fi

  # ── f. Claude fix ────────────────────────────────────────────────
  echo "[$(date +%H:%M:%S)] Running Claude fix..."
  FIX_FILE="$LOG_DIR/fix-${i}.md"

  # Stash any dirty/untracked state so Claude works on a clean tree.
  # This ensures we only commit Claude's changes, not pre-existing user edits.
  STASH_CREATED=false
  if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    git stash push --include-untracked -m "review-loop: pre-fix stash (iteration $i)" -q
    STASH_CREATED=true
    # Ensure stash is restored even if the script exits unexpectedly (set -e)
    trap 'echo "Warning: restoring stashed changes before exit..."; git stash pop -q 2>/dev/null || echo "Warning: stash pop failed; your changes are still in git stash."' EXIT
  fi

  export REVIEW_JSON
  FIX_PROMPT=$(envsubst < "$TEMPLATES_DIR/claude-fix.prompt.md")

  claude -p "$FIX_PROMPT" \
    --allowedTools "Edit,Read,Glob,Grep,Bash" \
    > "$FIX_FILE" 2>&1 || true

  echo "  Fix log saved to $FIX_FILE"

  # ── g. Commit & push fixes ──────────────────────────────────────
  # After Claude, only its changes are in the working tree (stash holds user edits).
  # Collect changed/new files as NUL-delimited list for whitespace safety.
  # Use a temp file because Bash strips NUL bytes in command substitution.
  FIX_FILES_NUL_FILE=$(mktemp)
  { git diff --name-only -z; git diff --cached --name-only -z; git ls-files --others --exclude-standard -z; } | awk -v RS='\0' -v ORS='\0' '!seen[$0]++' > "$FIX_FILES_NUL_FILE"
  if [[ ! -s "$FIX_FILES_NUL_FILE" ]]; then
    echo "  No file changes after fix — nothing to commit."
    rm -f "$FIX_FILES_NUL_FILE"
  else
    echo "[$(date +%H:%M:%S)] Committing fixes..."
    xargs -0 git add -- < "$FIX_FILES_NUL_FILE"
    git commit --pathspec-from-file="$FIX_FILES_NUL_FILE" --pathspec-file-nul \
      -m "fix(ai-review): apply iteration $i fixes

Auto-generated by review-loop.sh (iteration $i/$MAX_LOOP)"
    rm -f "$FIX_FILES_NUL_FILE"
    echo "  Committed."

    # Push to update PR (if remote tracking exists)
    if git rev-parse --abbrev-ref --symbolic-full-name "@{u}" &>/dev/null; then
      echo "[$(date +%H:%M:%S)] Pushing to remote..."
      git push
      echo "  Pushed."
    else
      echo "  No upstream set — skipping push. Run: git push -u origin $CURRENT_BRANCH"
    fi
  fi

  # Restore pre-existing user edits from stash
  if [[ "$STASH_CREATED" == true ]]; then
    trap - EXIT
    if ! git stash pop -q; then
      echo "  Error: stash pop had conflicts; resolve manually before rerunning."
      FINAL_STATUS="stash_conflict"
      break
    fi
  fi

  # ── h. Post iteration summary as PR comment ─────────────────────
  if [[ -n "$PR_NUMBER" ]]; then
    echo "[$(date +%H:%M:%S)] Posting PR comment..."

    # Build findings table
    FINDINGS_TABLE=$(echo "$REVIEW_JSON" | jq -r '
      .findings[] |
      "| \(.title) | \(.confidence_score) | `\(.code_location.absolute_file_path):\(.code_location.line_range.start)` |"
    ')

    # Read fix summary (extract the ## Fix Summary section)
    FIX_SUMMARY=""
    if [[ -f "$FIX_FILE" ]]; then
      FIX_SUMMARY=$(sed -n '/^## Fix Summary/,/^## /{ /^## Fix Summary/d; /^## /d; p; }' "$FIX_FILE")
      # If no second ## header, get everything after Fix Summary
      if [[ -z "$FIX_SUMMARY" ]]; then
        FIX_SUMMARY=$(sed -n '/^## Fix Summary/,${ /^## Fix Summary/d; p; }' "$FIX_FILE")
      fi
    fi

    COMMENT_BODY=$(cat <<EOF
### AI Review — Iteration $i / $MAX_LOOP

**Overall**: $OVERALL ($FINDINGS_COUNT findings)

<details>
<summary>Review Findings</summary>

| Finding | Confidence | Location |
|---------|-----------|----------|
$FINDINGS_TABLE

</details>

<details>
<summary>Fix Actions</summary>

$FIX_SUMMARY

</details>
EOF
)
    gh pr comment "$PR_NUMBER" --body "$COMMENT_BODY"
    echo "  PR comment posted."
  fi

  echo ""
done

# ── Summary ───────────────────────────────────────────────────────────
SUMMARY_FILE="$LOG_DIR/summary.md"

{
  echo "# Review Loop Summary"
  echo ""
  echo "- **Branch**: $CURRENT_BRANCH → $TARGET_BRANCH"
  echo "- **Max iterations**: $MAX_LOOP"
  echo "- **Final status**: $FINAL_STATUS"
  echo "- **Timestamp**: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo ""
  echo "## Iteration Logs"
  echo ""
  for f in "$LOG_DIR"/review-*.json; do
    [[ -e "$f" ]] || continue
    iter=$(basename "$f" | sed 's/review-//;s/.json//')
    count=$(jq '.findings | length' "$f" 2>/dev/null || echo "?")
    verdict=$(jq -r '.overall_correctness' "$f" 2>/dev/null || echo "?")
    echo "- **Iteration $iter**: $count findings, verdict: $verdict"
  done
} > "$SUMMARY_FILE"

echo ""
echo "═══════════════════════════════════════════════════════"
echo " Done. Status: $FINAL_STATUS"
echo " Summary: $SUMMARY_FILE"
echo "═══════════════════════════════════════════════════════"
