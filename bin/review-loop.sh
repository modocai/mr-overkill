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
  2. Claude fixes P0/P1 issues
  3. Auto-commit & push fixes to update PR
  4. Repeat until clean or max iterations

Examples:
  review-loop.sh -t main -n 3          # diff against main, max 3 loops
  review-loop.sh -n 5                  # diff against develop, max 5 loops
  review-loop.sh -n 1 --dry-run        # single review, no fixes
EOF
  exit 0
}

# ── Argument parsing ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--target)  TARGET_BRANCH="$2"; shift 2 ;;
    -n|--max-loop) MAX_LOOP="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=true; shift ;;
    -h|--help)    usage ;;
    *)            echo "Error: unknown option '$1'"; usage ;;
  esac
done

if [[ -z "$MAX_LOOP" ]]; then
  echo "Error: -n / --max-loop is required."
  echo ""
  usage
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

  REVIEW_PROMPT=$(envsubst < "$TEMPLATES_DIR/codex-review.prompt.md")

  codex exec \
    --sandbox read-only \
    -o "$REVIEW_FILE" \
    "$REVIEW_PROMPT" 2>&1 || true

  # ── c. Extract JSON from response ────────────────────────────────
  # Try direct jq parse first
  if jq empty "$REVIEW_FILE" 2>/dev/null; then
    REVIEW_JSON=$(cat "$REVIEW_FILE")
  else
    # Extract JSON from markdown fences or mixed text
    REVIEW_JSON=$(sed -n '/^```\(json\)\{0,1\}$/,/^```$/{ /^```/d; p; }' "$REVIEW_FILE" | head -1000)
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
    FINAL_STATUS="all_clear"
    break
  fi

  # Count P0/P1 findings
  P0P1_COUNT=$(echo "$REVIEW_JSON" | jq '[.findings[] | select(.priority <= 1)] | length')
  echo "  P0/P1 findings: $P0P1_COUNT"

  if [[ "$P0P1_COUNT" -eq 0 ]]; then
    echo "  No P0/P1 issues — only low-priority findings remain."
    FINAL_STATUS="only_low_priority"
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

  # Snapshot dirty files before Claude runs so we only commit its changes
  PRE_FIX_DIRTY=$(git diff --name-only)

  export REVIEW_JSON
  FIX_PROMPT=$(envsubst < "$TEMPLATES_DIR/claude-fix.prompt.md")

  claude -p "$FIX_PROMPT" \
    --allowedTools "Edit,Read,Glob,Grep,Bash" \
    > "$FIX_FILE" 2>&1 || true

  echo "  Fix log saved to $FIX_FILE"

  # ── g. Commit & push fixes ──────────────────────────────────────
  if git diff --quiet && git diff --cached --quiet; then
    echo "  No file changes after fix — nothing to commit."
  else
    echo "[$(date +%H:%M:%S)] Committing fixes..."
    # Only stage files changed by Claude (not in the pre-fix dirty set)
    POST_FIX_DIRTY=$(git diff --name-only)
    FIX_FILES=$(comm -13 <(echo "$PRE_FIX_DIRTY" | sort) <(echo "$POST_FIX_DIRTY" | sort))
    if [[ -z "$FIX_FILES" ]]; then
      echo "  No new changes from Claude fix — skipping commit."
    else
      # Stage only Claude-changed files and commit just those paths
      # to avoid accidentally including pre-staged unrelated files.
      echo "$FIX_FILES" | xargs git add --
      echo "$FIX_FILES" | xargs git commit -m "fix(ai-review): apply iteration $i fixes

Auto-generated by review-loop.sh (iteration $i/$MAX_LOOP)" --
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
