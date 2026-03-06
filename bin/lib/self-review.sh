#!/usr/bin/env bash
# Self-review sub-loop for overkill scripts.
# Source this file for library use, or run standalone for ad-hoc self-review.
# Requires: caller must set -euo pipefail before sourcing.
# Requires: common.sh must be sourced before this file (library mode).
#
# Library usage:
#   source "$SCRIPT_DIR/lib/self-review.sh"
#   SELF_REVIEW_SUMMARY=$(_self_review_subloop \
#     "$PRE_FIX_STATE" "$MAX_SUBLOOP" "$LOG_DIR" "$i" "$REVIEW_JSON")
#
# Standalone usage:
#   bin/lib/self-review.sh --max-subloop 4

[[ -n "${_SELF_REVIEW_SH_LOADED:-}" ]] && return 0
_SELF_REVIEW_SH_LOADED=1

# ── JSON Hook: inject refactoring_plan ───────────────────────────────
# Pipes: stdin = self-review JSON, $1 = original review JSON.
# Injects refactoring_plan from original into self-review output.
_sr_inject_refactoring_plan() {
  local _orig_json="$1"
  jq --argjson plan \
    "$(printf '%s' "$_orig_json" | jq '.refactoring_plan // null')" \
    'if $plan then . + {refactoring_plan: $plan} else . end'
}

# ── Self-Review Sub-Loop ─────────────────────────────────────────────
# $1 = pre_fix_state    snapshot file path (_snapshot_worktree result)
# $2 = max_subloop      maximum sub-iterations
# $3 = log_dir          log output directory
# $4 = iteration        outer iteration number (for log filenames)
# $5 = original_review_json  original Codex findings JSON string
#
# Optional env vars (override defaults):
#   SR_OPINION_PROMPT    re-fix opinion prompt filename (default: claude-fix.prompt.md)
#   SR_EXECUTE_PROMPT    re-fix execute prompt filename (default: claude-fix-execute.prompt.md)
#   SR_REFIX_JSON_HOOK   re-fix JSON transform function name (stdin=self_review_json, $1=original_json)
#   SR_DRY_RUN           if "true", review only — skip re-fix
#   SR_FIX_NITS          if "true", also flag nits and potential issues
#   SR_INITIAL_DIFF_FILE pre-generated diff for first iteration (branch diff mode)
#   SR_COMMIT_SNAPSHOT   snapshot file path — commit+push after each sub-iteration
#
# Required globals (read-only): CURRENT_BRANCH, TARGET_BRANCH, PROMPTS_DIR
#
# stdout → SELF_REVIEW_SUMMARY string (caller captures with $())
# stderr → progress messages (displayed on terminal)
# return 0 always (individual failures are recorded in summary)
_self_review_subloop() {
  local _pre_fix_state="$1"
  local _max_subloop="$2"
  local _log_dir="$3"
  local _iteration="$4"
  local _original_review_json="$5"

  local _opinion_prompt="${SR_OPINION_PROMPT:-claude-fix.prompt.md}"
  local _execute_prompt="${SR_EXECUTE_PROMPT:-claude-fix-execute.prompt.md}"
  local _refix_json_hook="${SR_REFIX_JSON_HOOK:-}"
  local _dry_run="${SR_DRY_RUN:-false}"
  local _initial_diff="${SR_INITIAL_DIFF_FILE:-}"
  local _fix_nits="${SR_FIX_NITS:-false}"

  local _summary="" _j _fix_files_tmp _self_review_file _self_review_prompt
  local _rc _self_review_json _sr_findings _sr_overall
  local _refix_file _refix_opinion_file _refix_input_json
  local _prev_sr_fingerprint=""
  local _sr_history=""

  export ITERATION="$_iteration"

  for (( _j=1; _j<=_max_subloop; _j++ )); do
    # First iteration can use a pre-generated diff (branch diff mode)
    if [[ $_j -eq 1 ]] && [[ -n "$_initial_diff" ]] && [[ -s "$_initial_diff" ]]; then
      export DIFF_FILE="$_initial_diff"
    elif [[ -n "$_initial_diff" ]]; then
      # Branch diff mode iter 2+: full branch diff (merge-base → working tree)
      # includes both committed branch changes and uncommitted re-fix edits
      export DIFF_FILE="$_log_dir/diff-${_iteration}-${_j}.diff"
      # Stage untracked files as intent-to-add so git diff can see them
      local _untracked_tmp; _untracked_tmp=$(mktemp)
      git ls-files --others --exclude-standard -z > "$_untracked_tmp"
      if [[ -s "$_untracked_tmp" ]]; then
        xargs -0 git add --intent-to-add -- < "$_untracked_tmp" 2>/dev/null || true
      fi
      # Exclude log directory from diff to avoid reviewing script artifacts
      local _log_prefix
      _log_prefix=$(cd "$_log_dir" 2>/dev/null && git rev-parse --show-prefix 2>/dev/null) || _log_prefix=""
      git diff "$(git merge-base "$TARGET_BRANCH" "$CURRENT_BRANCH")" \
        ${_log_prefix:+-- ":(exclude)${_log_prefix%/}"} > "$DIFF_FILE"
      # Undo intent-to-add to preserve original index state
      if [[ -s "$_untracked_tmp" ]]; then
        xargs -0 git reset --quiet -- < "$_untracked_tmp" 2>/dev/null || true
      fi
      rm -f "$_untracked_tmp"
      if [[ ! -s "$DIFF_FILE" ]]; then
        echo "  No remaining diff — skipping self-review." >&2
        break
      fi
    else
      # Check if fix produced any changes vs pre-fix snapshot
      if ! _fix_files_tmp=$(_changed_files_since_snapshot "$_pre_fix_state"); then
        echo "  No working tree changes from fix — skipping self-review." >&2
        break
      fi

      # Dump diff for only files changed by the fix (exclude pre-existing dirty files)
      # Stage untracked files as intent-to-add so git diff HEAD can see them
      xargs -0 git add --intent-to-add -- < "$_fix_files_tmp" 2>/dev/null || true
      export DIFF_FILE="$_log_dir/diff-${_iteration}-${_j}.diff"
      xargs -0 git diff HEAD -- < "$_fix_files_tmp" > "$DIFF_FILE"
      # Immediately undo intent-to-add so we never clobber pre-existing staged state
      xargs -0 git reset --quiet -- < "$_fix_files_tmp" 2>/dev/null || true
      rm -f "$_fix_files_tmp"
    fi

    echo "[$(date +%H:%M:%S)] Running Claude self-review (sub-iteration $_j/$_max_subloop)..." >&2
    _self_review_file="$_log_dir/self-review-${_iteration}-${_j}.json"

    # Self-review prompt always references the original Codex findings
    export REVIEW_JSON="$_original_review_json"

    # Inject extra review guidelines when fix-nits mode is enabled
    if [[ "$_fix_nits" == true ]]; then
      export EXTRA_REVIEW_GUIDELINES="$(cat <<'GUIDELINES'
6. **Fix nits and potential issues**: Beyond verifying the original fixes, also flag:
   - Style inconsistencies in the changed code (naming, formatting)
   - Potential edge cases or error handling gaps
   - Minor improvements that are low-risk and localized to the changed files
   - Do NOT flag issues in unchanged code — only in files touched by the diff
7. **Strict correctness in fix-nits mode**: When any finding remains (including nits and style issues), you MUST set `overall_correctness` to `"patch is incorrect"`. Only return `"patch is correct"` when there are truly zero findings.
GUIDELINES
)"
    else
      export EXTRA_REVIEW_GUIDELINES=""
    fi

    # Inject sub-iteration history so the reviewer knows what was already flagged
    # printf -v prevents shell re-expansion of $ characters inside _sr_history
    if [[ -n "$_sr_history" ]]; then
      printf -v SELF_REVIEW_HISTORY '\n## Previous Sub-Iteration Findings\n\nThe following findings were flagged in previous sub-iterations and re-fix was already attempted.\nDo NOT repeat these same findings. Only flag NEW issues or issues that the re-fix introduced.\nIf a previous finding persists after re-fix, you may re-flag it but explicitly note that the previous fix attempt failed and suggest a different approach.\n\n%s' "$_sr_history"
      export SELF_REVIEW_HISTORY
    else
      export SELF_REVIEW_HISTORY=""
    fi

    _self_review_prompt=$(envsubst '$CURRENT_BRANCH $TARGET_BRANCH $ITERATION $REVIEW_JSON $DIFF_FILE $EXTRA_REVIEW_GUIDELINES $SELF_REVIEW_HISTORY' < "$PROMPTS_DIR/claude-self-review.prompt.md")

    # Pre-flight budget check
    if ! _wait_for_budget "claude" "${BUDGET_SCOPE:-micro}"; then
      echo "  Warning: Claude budget timeout before self-review." >&2
      break
    fi

    # Claude self-review — tool access for git diff, file reading, etc.
    if ! printf '%s' "$_self_review_prompt" | _retry_claude_cmd "$_self_review_file" "self-review" \
      claude -p - \
      --allowedTools "Read,Glob,Grep"; then
      echo "  Warning: self-review failed (sub-iteration $_j). Continuing with current fixes." >&2
      _summary="${_summary}Sub-iteration $_j: self-review failed\n"
      break
    fi

    # JSON parsing (same logic as codex review)
    if [[ ! -s "$_self_review_file" ]]; then
      echo "  Warning: self-review produced empty output (sub-iteration $_j). Continuing with current fixes." >&2
      _summary="${_summary}Sub-iteration $_j: empty output\n"
      break
    fi
    if ! _self_review_json=$(_parse_review_json "$_self_review_file" "self-review"); then
      echo "  Continuing with current fixes." >&2
      _summary="${_summary}Sub-iteration $_j: parse error\n"
      break
    fi

    # Guard against schema-invalid JSON (e.g. "findings": "oops" instead of array)
    if ! printf '%s' "$_self_review_json" | jq -e '.findings | type == "array"' >/dev/null 2>&1; then
      echo "  Warning: self-review output has invalid findings shape (sub-iteration $_j). Continuing with current fixes." >&2
      _summary="${_summary}Sub-iteration $_j: invalid findings schema\n"
      break
    fi

    _sr_findings=$(printf '%s' "$_self_review_json" | jq '.findings | length')
    _sr_overall=$(printf '%s' "$_self_review_json" | jq -r '.overall_correctness')
    echo "  Self-review: $_sr_findings findings | $_sr_overall" >&2

    if [[ "$_sr_findings" -eq 0 ]] && [[ "$_sr_overall" == "patch is correct" ]]; then
      echo "  Self-review passed — fixes are clean." >&2
      _summary="${_summary}Sub-iteration $_j: 0 findings — passed\n"
      break
    fi

    # Convergence check: same findings after re-fix means we're not making progress
    local _sr_fingerprint
    _sr_fingerprint=$(printf '%s' "$_self_review_json" | jq -r '[(.findings // [])[] | select(type == "object") | "\(.title // "")@\(.code_location?.file_path // "")@\(.body // "" | tostring | .[0:60])"] | sort | join("|")')
    if [[ $_j -gt 1 ]] && [[ "$_sr_fingerprint" == "$_prev_sr_fingerprint" ]]; then
      echo "  Findings unchanged ($_sr_findings) after re-fix — stopping (not converging)." >&2
      _summary="${_summary}Sub-iteration $_j: $_sr_findings findings — not converging\n"
      break
    fi
    _prev_sr_fingerprint="$_sr_fingerprint"

    # Dry-run: report findings but skip re-fix
    if [[ "$_dry_run" == true ]]; then
      echo "  Self-review dry-run — skipping re-fix." >&2
      _summary="${_summary}Sub-iteration $_j: $_sr_findings findings — dry-run\n"
      break
    fi

    # Claude re-fix (two-step: opinion → execute)
    _refix_file="$_log_dir/refix-${_iteration}-${_j}.md"
    _refix_opinion_file="$_log_dir/refix-opinion-${_iteration}-${_j}.md"

    # Apply JSON hook if configured (e.g. inject refactoring_plan)
    _refix_input_json="$_self_review_json"
    if [[ -n "$_refix_json_hook" ]]; then
      if ! declare -F "$_refix_json_hook" >/dev/null 2>&1; then
        echo "  Warning: SR_REFIX_JSON_HOOK='$_refix_json_hook' is not a defined function — skipping hook." >&2
      else
        _refix_input_json=$(printf '%s' "$_self_review_json" | "$_refix_json_hook" "$_original_review_json")
      fi
    fi

    # Inject sub-iteration history so the fixer knows what was already tried
    # printf -v prevents shell re-expansion of $ characters inside _sr_history
    if [[ -n "$_sr_history" ]]; then
      printf -v FIX_HISTORY '\n## Previous Fix Attempts\n\nPrevious sub-iterations already attempted fixes for the findings below.\nIf the same or similar findings appear again, try a DIFFERENT approach from what was done before.\nDo not revert previous fixes unless they introduced new bugs.\n\n%s' "$_sr_history"
      export FIX_HISTORY
    else
      export FIX_HISTORY=""
    fi

    if ! _claude_two_step_fix "$_refix_input_json" "$_refix_opinion_file" "$_refix_file" "re-fix" \
      "$_opinion_prompt" "$_execute_prompt" >&2; then
      _summary="${_summary}Sub-iteration $_j: $_sr_findings findings — re-fix failed\n"
      break
    fi
    # Commit per sub-iteration if snapshot path provided
    if [[ -n "${SR_COMMIT_SNAPSHOT:-}" ]] && [[ "$_dry_run" != true ]]; then
      if ! git diff --quiet || ! git diff --cached --quiet \
         || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
        if _commit_and_push "$SR_COMMIT_SNAPSHOT" \
          "fix(ai-review): apply self-review sub-iteration $_j" \
          "$CURRENT_BRANCH" >&2; then
          # Refresh snapshot for next iteration
          local _new_snap
          _new_snap=$(_snapshot_worktree)
          cp "$_new_snap" "$SR_COMMIT_SNAPSHOT"
          rm -f "$_new_snap"
        else
          echo "  Warning: commit/push failed (sub-iteration $_j). Continuing." >&2
        fi
      fi
    fi
    # Accumulate findings for next sub-iteration's history context
    local _fence='```'
    _sr_history="${_sr_history}### Sub-iteration ${_j} (${_sr_findings} findings)
${_fence}json
${_self_review_json}
${_fence}

"
    _summary="${_summary}Sub-iteration $_j: $_sr_findings findings — re-fixed\n"
  done

  printf '%s' "$_summary"
}

# ── Standalone mode ──────────────────────────────────────────────────
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -euo pipefail

  _SELF_REVIEW_SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  source "$_SELF_REVIEW_SCRIPT_DIR/common.sh"

  # Defaults
  _MAX_SUBLOOP=4
  _DRY_RUN=false
  _FIX_NITS=false
  _REFACTORING_PLAN_FILE=""
  _LOG_DIR=""
  _ITERATION=1
  _TARGET=""

  usage() {
    cat <<'EOF'
Usage: self-review.sh [OPTIONS]

Self-review changes and optionally re-fix issues found.
Works with uncommitted changes or branch diff (clean tree + -t <branch>).

Options:
  --max-subloop <N>         Maximum sub-iterations (default: 4)
  --dry-run                 Review only, do not apply re-fixes
  --fix-nits                Also fix nits and potential issues in changed files
  --diagnostic-log          Save full Claude event stream to .stream.jsonl sidecar files
  --refactoring-plan <file> Load refactoring_plan from JSON file (scope context)
  --log-dir <dir>           Log directory (default: temporary directory)
  --iteration <N>           Iteration number for log filenames (default: 1)
  -t, --target <branch>     Target branch for diff context (default: current branch)
  -h, --help                Show this help message

Examples:
  self-review.sh                             # review uncommitted changes
  self-review.sh -t develop --dry-run        # review branch diff vs develop
  self-review.sh -t main --max-subloop 2     # review + re-fix, max 2 iterations
  self-review.sh --refactoring-plan plan.json # include refactoring plan context
EOF
    exit "${1:-0}"
  }

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --max-subloop)
        _require_arg "$1" "$#" || usage 1
        _MAX_SUBLOOP="$2"; shift 2 ;;
      --dry-run)         _DRY_RUN=true; shift ;;
      --fix-nits)        _FIX_NITS=true; shift ;;
      --diagnostic-log)  DIAGNOSTIC_LOG=true; shift ;;
      --refactoring-plan)
        _require_arg "$1" "$#" || usage 1
        _REFACTORING_PLAN_FILE="$2"; shift 2 ;;
      --log-dir)
        _require_arg "$1" "$#" || usage 1
        _LOG_DIR="$2"; shift 2 ;;
      --iteration)
        _require_arg "$1" "$#" || usage 1
        _ITERATION="$2"; shift 2 ;;
      -t|--target)
        _require_arg "$1" "$#" || usage 1
        _TARGET="$2"; shift 2 ;;
      -h|--help) usage ;;
      *)         echo "Error: unknown option '$1'"; usage 1 ;;
    esac
  done

  # Validation
  _require_pos_int "--max-subloop" "$_MAX_SUBLOOP"
  _require_pos_int "--iteration" "$_ITERATION"

  if [[ -n "$_REFACTORING_PLAN_FILE" ]] && [[ ! -f "$_REFACTORING_PLAN_FILE" ]]; then
    echo "Error: refactoring plan file not found: $_REFACTORING_PLAN_FILE"
    exit 1
  fi

  # Prerequisites
  _require_core
  check_cmd claude

  # Set globals
  CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
  TARGET_BRANCH="${_TARGET:-$CURRENT_BRANCH}"
  export CURRENT_BRANCH TARGET_BRANCH DIAGNOSTIC_LOG

  # Determine review mode: dirty working tree or branch diff
  _BRANCH_DIFF_MODE=false
  if git diff --quiet && git diff --cached --quiet \
     && [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
    # Clean tree — fall back to branch diff mode
    if [[ "$TARGET_BRANCH" == "$CURRENT_BRANCH" ]]; then
      echo "Error: working tree is clean and no target branch specified."
      echo "Use -t <branch> to review branch diff, or make changes first."
      exit 1
    fi
    if ! git merge-base "$TARGET_BRANCH" "$CURRENT_BRANCH" &>/dev/null; then
      echo "Error: cannot find merge-base between '$TARGET_BRANCH' and '$CURRENT_BRANCH'."
      echo "Check that '$TARGET_BRANCH' is a valid branch/ref."
      exit 1
    fi
    if git diff --quiet "$TARGET_BRANCH...$CURRENT_BRANCH"; then
      echo "Error: no diff between $TARGET_BRANCH and $CURRENT_BRANCH — nothing to review."
      exit 1
    fi
    _BRANCH_DIFF_MODE=true
  fi

  PROMPTS_DIR="${PROMPTS_DIR:-$_SELF_REVIEW_SCRIPT_DIR/../../prompts/active}"
  if [[ ! -d "$PROMPTS_DIR" ]]; then
    echo "Error: prompts directory not found: $PROMPTS_DIR" >&2
    exit 1
  fi

  if [[ ! -f "$PROMPTS_DIR/claude-self-review.prompt.md" ]]; then
    echo "Error: self-review prompt not found: $PROMPTS_DIR/claude-self-review.prompt.md" >&2
    exit 1
  fi

  # Log directory
  _AUTO_LOG_DIR=false
  if [[ -z "$_LOG_DIR" ]]; then
    _LOG_DIR=$(mktemp -d)
    _AUTO_LOG_DIR=true
    echo "Using temporary log directory: $_LOG_DIR"
  else
    mkdir -p "$_LOG_DIR"
  fi

  # Empty baseline — treat all current dirty files as "changes to review"
  PRE_FIX_STATE=$(mktemp)
  _standalone_cleanup() {
    rm -f "$PRE_FIX_STATE" "${_COMMIT_SNAPSHOT:-}"
    if [[ "$_AUTO_LOG_DIR" == true ]] && [[ -d "$_LOG_DIR" ]]; then
      rm -rf "$_LOG_DIR"
    fi
  }
  trap _standalone_cleanup EXIT

  # ── Initial Claude review (standalone mode) ──────────────────────
  # Use codex-review.prompt.md to generate findings before entering
  # the self-review sub-loop, mirroring the review-loop.sh flow.
  # The diff is pre-generated and appended to the prompt so Claude
  # needs only read-only tools (no Bash access required).
  _REVIEW_JSON='{"findings":[],"overall_correctness":"not reviewed"}'
  _initial_review_file="$_LOG_DIR/review-initial.json"

  if [[ -f "$PROMPTS_DIR/codex-review.prompt.md" ]]; then
    export ITERATION=0
    _initial_prompt=$(envsubst '$CURRENT_BRANCH $TARGET_BRANCH $ITERATION' < "$PROMPTS_DIR/codex-review.prompt.md")
    unset ITERATION

    # Pre-generate diff and append to prompt so Claude doesn't need Bash
    if [[ "$_BRANCH_DIFF_MODE" == true ]]; then
      _initial_diff_content=$(git diff "$TARGET_BRANCH...$CURRENT_BRANCH")
    else
      # Dirty-tree: diff of uncommitted changes (including untracked files)
      _untracked_tmp=$(mktemp)
      git ls-files --others --exclude-standard -z > "$_untracked_tmp"
      if [[ -s "$_untracked_tmp" ]]; then
        xargs -0 git add --intent-to-add -- < "$_untracked_tmp" 2>/dev/null || true
      fi
      _initial_diff_content=$(git diff HEAD)
      if [[ -s "$_untracked_tmp" ]]; then
        xargs -0 git reset --quiet -- < "$_untracked_tmp" 2>/dev/null || true
      fi
      rm -f "$_untracked_tmp"
    fi
    _initial_prompt="${_initial_prompt}

## Diff

\`\`\`diff
${_initial_diff_content}
\`\`\`"

    # Pre-flight budget check
    if ! _wait_for_budget "claude" "${BUDGET_SCOPE:-micro}"; then
      echo "  Warning: Claude budget timeout before initial review." >&2
    else
      echo "[$(date +%H:%M:%S)] Running initial Claude review..."
      _initial_ok=true
      if ! printf '%s' "$_initial_prompt" | _retry_claude_cmd "$_initial_review_file" "initial review" \
        claude -p - \
        --allowedTools "Read,Glob,Grep"; then
        echo "  Warning: initial review failed. Falling back to empty findings." >&2
        _initial_ok=false
      fi

      if [[ "$_initial_ok" == true ]] && [[ -s "$_initial_review_file" ]]; then
        _rc=0
        _initial_json=$(_extract_json_from_file "$_initial_review_file") || _rc=$?
        if [[ $_rc -eq 0 ]]; then
          _initial_count=$(printf '%s' "$_initial_json" | jq '.findings | length')
          echo "  Initial review: $_initial_count findings"
          _REVIEW_JSON=$(printf '%s' "$_initial_json" | _normalize_review_json_paths)
        else
          echo "  Warning: could not parse initial review output. Falling back to empty findings." >&2
        fi
      elif [[ "$_initial_ok" == true ]]; then
        echo "  Warning: initial review produced empty output. Falling back to empty findings." >&2
      fi
    fi
  else
    echo "  Warning: codex-review.prompt.md not found. Skipping initial review." >&2
  fi
  if [[ -n "$_REFACTORING_PLAN_FILE" ]]; then
    _plan=$(jq '.' "$_REFACTORING_PLAN_FILE") || {
      echo "Error: invalid JSON in refactoring plan file: $_REFACTORING_PLAN_FILE"
      exit 1
    }
    _REVIEW_JSON=$(printf '%s' "$_REVIEW_JSON" | jq --argjson plan "$_plan" '. + {refactoring_plan: $plan}')
  fi

  _MODE_LABEL="uncommitted changes"
  if [[ "$_BRANCH_DIFF_MODE" == true ]]; then
    _MODE_LABEL="branch diff ($TARGET_BRANCH...$CURRENT_BRANCH)"
  fi

  echo "═══════════════════════════════════════════════════════"
  echo " Self-Review: $CURRENT_BRANCH (target: $TARGET_BRANCH)"
  echo " Mode: $_MODE_LABEL"
  echo " Max sub-iterations: $_MAX_SUBLOOP | Dry-run: $_DRY_RUN | Fix-nits: $_FIX_NITS"
  echo "═══════════════════════════════════════════════════════"
  echo ""

  # Generate branch diff for first iteration if in branch diff mode
  _INITIAL_DIFF=""
  if [[ "$_BRANCH_DIFF_MODE" == true ]]; then
    _INITIAL_DIFF="$_LOG_DIR/branch-diff.diff"
    git diff "$TARGET_BRANCH...$CURRENT_BRANCH" > "$_INITIAL_DIFF"
  fi

  # Snapshot current state so we only commit files changed by the sub-loop,
  # not pre-existing dirty files that happen to be in the working tree.
  _COMMIT_SNAPSHOT=$(_snapshot_worktree)

  # Call the sub-loop (commits per sub-iteration via SR_COMMIT_SNAPSHOT)
  SUMMARY=$(
    if [[ "$_DRY_RUN" == true ]]; then SR_DRY_RUN=true; fi
    if [[ "$_FIX_NITS" == true ]]; then SR_FIX_NITS=true; fi
    if [[ -n "$_REFACTORING_PLAN_FILE" ]]; then SR_REFIX_JSON_HOOK="_sr_inject_refactoring_plan"; fi
    if [[ -n "$_INITIAL_DIFF" ]]; then SR_INITIAL_DIFF_FILE="$_INITIAL_DIFF"; fi
    if [[ "$_DRY_RUN" != true ]] && [[ "$_BRANCH_DIFF_MODE" == true ]]; then SR_COMMIT_SNAPSHOT="$_COMMIT_SNAPSHOT"; fi
    _self_review_subloop \
      "$PRE_FIX_STATE" "$_MAX_SUBLOOP" "$_LOG_DIR" "$_ITERATION" "$_REVIEW_JSON"
  )
  rm -f "$_COMMIT_SNAPSHOT"

  echo ""
  echo "═══════════════════════════════════════════════════════"
  if [[ -n "$SUMMARY" ]]; then
    echo " Self-Review Summary:"
    printf '%b' "$SUMMARY" | sed 's/^/  /'
  else
    echo " No self-review iterations executed."
  fi
  echo " Logs: $_LOG_DIR"
  echo "═══════════════════════════════════════════════════════"
fi
