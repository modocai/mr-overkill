"""Self-review sub-loop for verifying and re-fixing Claude's changes.

Ports ``_self_review_subloop`` from ``bin/lib/self-review.sh``.
Implements the :class:`SelfReviewFn` Protocol from ``loop_engine``.
"""

from __future__ import annotations

import json
import logging
import string
import subprocess
from pathlib import Path

from mr_overkill.git_ops import changed_files_since_snapshot
from mr_overkill.json_extract import (
    inject_refactoring_plan,
    parse_review_json,
)
from mr_overkill.models import (
    BudgetCheckFn,
    BudgetScope,
    FixFn,
    RetryFn,
    WorktreeSnapshot,
)

logger = logging.getLogger(__name__)

_FIX_NITS_GUIDELINES = """\
6. **Fix nits and potential issues**: Beyond verifying \
the original fixes, also flag:
   - Style inconsistencies in the changed code (naming, formatting)
   - Potential edge cases or error handling gaps
   - Minor improvements that are low-risk and localized to the changed files
   - Do NOT flag issues in unchanged code — only in files touched by the diff
7. **Strict correctness in fix-nits mode**: When any finding remains \
(including nits and style issues), you MUST set `overall_correctness` \
to `"patch is incorrect"`. Only return `"patch is correct"` when there \
are truly zero findings."""


def self_review_subloop(
    pre_fix_snapshot: list[WorktreeSnapshot],
    max_subloop: int,
    log_dir: Path,
    iteration: int,
    review_json_str: str,
    *,
    retry_fn: RetryFn,
    budget_fn: BudgetCheckFn,
    fix_fn: FixFn,
    prompts_dir: Path,
    current_branch: str,
    target_branch: str,
    budget_scope: BudgetScope = BudgetScope.MICRO,
    dry_run: bool = False,
    fix_nits: bool = False,
    original_review_json: dict[str, object] | None = None,
    cwd: Path | None = None,
) -> str:
    """Run self-review sub-loop: review fixes then re-fix if needed.

    Parameters
    ----------
    pre_fix_snapshot
        Worktree snapshot from before fixes were applied.
    max_subloop
        Maximum sub-iterations.
    log_dir
        Directory for log files.
    iteration
        Outer loop iteration number (for log filenames).
    review_json_str
        Original review findings as JSON string.
    retry_fn, budget_fn, fix_fn
        Protocol-injected dependencies.
    prompts_dir
        Directory containing prompt templates.
    current_branch, target_branch
        Branch names for prompt substitution.
    budget_scope
        Scope for budget checks (default: micro).
    dry_run
        If True, review only — skip re-fix.
    original_review_json
        Parsed original review dict (for refactoring_plan injection).
    cwd
        Working directory for git operations.

    Returns
    -------
    str
        Summary string of sub-iteration results.
    """
    summary_parts: list[str] = []
    prev_fingerprint = ""
    sr_history = ""

    for j in range(1, max_subloop + 1):
        # Check if fix produced changes
        changed = changed_files_since_snapshot(
            pre_fix_snapshot, cwd=cwd,
            exclude_prefix=".review-loop/logs/",
        )
        if not changed:
            logger.info("No working tree changes from fix — skipping self-review.")
            break

        # Generate diff of changed files
        diff_file = log_dir / f"diff-{iteration}-{j}.diff"
        _generate_diff(changed, diff_file, cwd)

        if not diff_file.is_file() or diff_file.stat().st_size == 0:
            logger.info("Empty diff — skipping self-review.")
            break

        logger.info(
            "Running Claude self-review (sub-iteration %d/%d)...",
            j,
            max_subloop,
        )

        sr_file = log_dir / f"self-review-{iteration}-{j}.json"

        # Pre-flight budget check
        if not budget_fn("claude", budget_scope, 0):
            logger.warning("Claude budget timeout before self-review.")
            break

        # Run self-review
        sr_prompt_file = prompts_dir / "claude-self-review.prompt.md"
        if not sr_prompt_file.is_file():
            logger.warning(
                "Self-review prompt not found: %s", sr_prompt_file
            )
            break

        extra_guidelines = _FIX_NITS_GUIDELINES if fix_nits else ""
        prompt_vars = {
            "CURRENT_BRANCH": current_branch,
            "TARGET_BRANCH": target_branch,
            "ITERATION": str(iteration),
            "REVIEW_JSON": review_json_str,
            "DIFF_FILE": str(diff_file),
            "EXTRA_REVIEW_GUIDELINES": extra_guidelines,
            "SELF_REVIEW_HISTORY": _build_history_prompt(sr_history),
        }
        tmpl = string.Template(
            sr_prompt_file.read_text(encoding="utf-8")
        )
        prompt_text = tmpl.safe_substitute(prompt_vars)

        ok = retry_fn(
            sr_file,
            "self-review",
            [
                "claude", "-p", "-",
                "--allowedTools", "Read,Glob,Grep",
            ],
            stdin=prompt_text,
        )
        if not ok:
            logger.warning(
                "Self-review failed (sub-iteration %d). "
                "Continuing with current fixes.",
                j,
            )
            summary_parts.append(f"Sub-iteration {j}: self-review failed")
            break

        # Parse self-review JSON
        if not sr_file.is_file() or sr_file.stat().st_size == 0:
            logger.warning(
                "Self-review produced empty output (sub-iteration %d).", j
            )
            summary_parts.append(f"Sub-iteration {j}: empty output")
            break

        sr_data, _rc = parse_review_json(sr_file, "self-review")
        if sr_data is None:
            summary_parts.append(f"Sub-iteration {j}: parse error")
            break

        findings = sr_data.get("findings", [])
        if not isinstance(findings, list):
            logger.warning(
                "Self-review has invalid findings shape (sub-iteration %d).",
                j,
            )
            summary_parts.append(
                f"Sub-iteration {j}: invalid findings schema"
            )
            break

        sr_count = len(findings)
        sr_overall = sr_data.get("overall_correctness", "?")
        logger.info("Self-review: %d findings | %s", sr_count, sr_overall)

        # All clear?
        if sr_count == 0 and sr_overall == "patch is correct":
            logger.info("Self-review passed — fixes are clean.")
            summary_parts.append(
                f"Sub-iteration {j}: 0 findings — passed"
            )
            break

        # Convergence check
        fingerprint = _compute_fingerprint(findings)
        if j > 1 and fingerprint == prev_fingerprint:
            logger.info(
                "Findings unchanged (%d) after re-fix — "
                "stopping (not converging).",
                sr_count,
            )
            summary_parts.append(
                f"Sub-iteration {j}: {sr_count} findings — not converging"
            )
            break
        prev_fingerprint = fingerprint

        # Dry-run: report findings but skip re-fix
        if dry_run:
            logger.info("Self-review dry-run — skipping re-fix.")
            summary_parts.append(
                f"Sub-iteration {j}: {sr_count} findings — dry-run"
            )
            break

        # Re-fix using fix_fn
        refix_input = sr_data
        if original_review_json:
            plan = original_review_json.get("refactoring_plan")
            if plan and isinstance(plan, dict):
                refix_input = inject_refactoring_plan(sr_data, plan)

        refix_json_str = json.dumps(refix_input)
        if not fix_fn(
            refix_json_str,
            f"re-fix-{iteration}-{j}",
            fix_history=_build_fix_history(sr_history),
        ):
            summary_parts.append(
                f"Sub-iteration {j}: {sr_count} findings — re-fix failed"
            )
            break

        # Accumulate history
        sr_history += (
            f"### Sub-iteration {j} ({sr_count} findings)\n"
            f"```json\n{json.dumps(sr_data)}\n```\n\n"
        )
        summary_parts.append(
            f"Sub-iteration {j}: {sr_count} findings — re-fixed"
        )

    return "\n".join(summary_parts)


def _generate_diff(
    changed_files: list[str],
    output: Path,
    cwd: Path | None,
) -> None:
    """Generate a diff for the given files against HEAD."""
    if not changed_files:
        output.write_text("")
        return

    # Stage untracked as intent-to-add so git diff can see them
    subprocess.run(
        ["git", "add", "--intent-to-add", "--", *changed_files],
        cwd=cwd,
        capture_output=True,
        check=False,
    )

    result = subprocess.run(
        ["git", "diff", "HEAD", "--", *changed_files],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    output.write_text(result.stdout, encoding="utf-8")

    # Undo intent-to-add
    subprocess.run(
        ["git", "reset", "--quiet", "--", *changed_files],
        cwd=cwd,
        capture_output=True,
        check=False,
    )


def _compute_fingerprint(findings: list[object]) -> str:
    """Compute a stable fingerprint of findings for convergence check."""
    parts: list[str] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        title = f.get("title", "")
        loc = f.get("code_location", {})
        fpath = loc.get("file_path", "") if isinstance(loc, dict) else ""
        line_range = loc.get("line_range", {}) if isinstance(loc, dict) else {}
        body = str(f.get("body", ""))[:200]
        parts.append(f"{title}@{fpath}@{line_range}@{body}")
    parts.sort()
    return "|".join(parts)


def _build_history_prompt(sr_history: str) -> str:
    """Build the SELF_REVIEW_HISTORY prompt section."""
    if not sr_history:
        return ""
    return (
        "\n## Previous Sub-Iteration Findings\n\n"
        "The following findings were flagged in previous sub-iterations "
        "and re-fix was already attempted.\n"
        "If any of these issues STILL exist in the current diff, re-flag them.\n"
        "Also flag any NEW issues introduced by the re-fix.\n\n"
        + sr_history
    )


def _build_fix_history(sr_history: str) -> str:
    """Build the FIX_HISTORY prompt section for re-fix attempts."""
    if not sr_history:
        return ""
    return (
        "\n## Previous Fix Attempts\n\n"
        "Previous sub-iterations already attempted fixes for the findings below.\n"
        "If the same or similar findings appear again, try a DIFFERENT approach "
        "from what was done before.\n"
        "Do not revert previous fixes unless they introduced new bugs.\n\n"
        + sr_history
    )
