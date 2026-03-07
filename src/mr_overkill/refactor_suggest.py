"""Refactor-suggest entry point.

Ports ``bin/refactor-suggest.sh`` to Python: budget-aware scope resolution,
branch creation, analysis loop, draft PR creation, and optional review-loop
chaining.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime

from mr_overkill.agents import (
    create_fix_agent,
    create_review_agent,
    create_self_review_agent,
)
from mr_overkill.budget import budget_sufficient
from mr_overkill.budget.claude import check_token_budget as claude_budget
from mr_overkill.budget.codex import check_token_budget as codex_budget
from mr_overkill.git_ops import git_all_dirty, stash_allowlisted, unstash_allowlisted
from mr_overkill.loop_engine import PreFixConfirmFn, review_fix_loop
from mr_overkill.models import (
    BudgetScope,
    BudgetStatus,
    FinalStatus,
    LoopConfig,
)

logger = logging.getLogger(__name__)


# ── Scope resolution ─────────────────────────────────────────────────


def resolve_auto_scope(
    tools: list[str] | None = None,
) -> str | None:
    """Resolve 'auto' scope based on current budget levels.

    Returns ``'module'``, ``'micro'``, or ``None`` if budget is too low.
    """
    if tools is None:
        tools = ["claude", "codex"]

    max_pct = 0
    max_7d = 0

    for tool in tools:
        status = _get_budget_status(tool)
        pct = status.five_hour_used_pct or 0
        d7 = status.seven_day_used_pct or 0

        if d7 >= 100:
            logger.error(
                "%s 7-day budget exhausted (%d%%).", tool, d7
            )
            return None

        max_pct = max(max_pct, pct)
        max_7d = max(max_7d, d7)

    # Build worst-case status for policy check
    worst = BudgetStatus(
        five_hour_used_pct=max_pct,
        seven_day_used_pct=max_7d,
        tokens_used=0,
        mode="synthetic",
        tier="",
        resets_at=None,
    )

    if budget_sufficient(BudgetScope.MODULE, worst):
        return "module"
    if budget_sufficient(BudgetScope.MICRO, worst):
        return "micro"

    logger.error(
        "Budget too low for any scope (5h: %d%%, 7d: %d%%).",
        max_pct,
        max_7d,
    )
    return None


def _get_budget_status(tool: str) -> BudgetStatus:
    """Fetch budget status for a tool."""
    if tool == "claude":
        return claude_budget()
    if tool == "codex":
        return codex_budget()
    return BudgetStatus(
        five_hour_used_pct=0,
        seven_day_used_pct=None,
        tokens_used=0,
        mode="unknown",
        tier="",
        resets_at=None,
    )


# ── Branch management ────────────────────────────────────────────────


_ALLOWLISTED_FILES = [
    ".gitignore",
    ".reviewlooprc",
    ".refactorsuggestrc",
    ".review-loop/.reviewlooprc",
    ".review-loop/.refactorsuggestrc",
]


def create_refactor_branch(
    scope: str,
    target_branch: str,
) -> str | None:
    """Create and switch to a refactor branch.

    Returns the branch name, or None on failure.
    """
    ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    branch = f"refactor/{scope}-{ts}"

    stashed = False
    try:
        stashed = stash_allowlisted(_ALLOWLISTED_FILES)
    except RuntimeError:
        logger.error("Failed to stash allowlisted files before branch creation.")
        return None

    result = subprocess.run(
        ["git", "checkout", "-b", branch, target_branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.error("Failed to create branch %s: %s", branch, result.stderr)
        if stashed:
            unstash_allowlisted()
        return None

    if stashed and not unstash_allowlisted():
        logger.error("Failed to restore stashed files. Check 'git stash list'.")
        return None

    logger.info("Created branch: %s (from %s)", branch, target_branch)
    return branch


# ── Draft PR creation ────────────────────────────────────────────────


def _detect_push_remote() -> str | None:
    """Return ``origin`` if available, otherwise the first configured remote."""
    result = subprocess.run(
        ["git", "remote"],
        capture_output=True,
        text=True,
        check=False,
    )
    remotes = result.stdout.strip().splitlines()
    if "origin" in remotes:
        return "origin"
    return remotes[0] if remotes else None


def create_draft_pr(
    scope: str,
    target_branch: str,
    current_branch: str,
    max_loop: int,
    final_status: str,
) -> bool:
    """Create a draft PR for the refactoring work.

    Returns True on success.
    """
    # Check if there are commits ahead
    result = subprocess.run(
        ["git", "rev-list", "--count", f"{target_branch}..{current_branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.error("Failed to count commits ahead of %s", target_branch)
        return False
    ahead = int(result.stdout.strip())
    if ahead == 0:
        logger.info("No refactoring commits — skipping PR creation.")
        return True

    # Ensure branch is pushed
    upstream = subprocess.run(
        [
            "git", "rev-parse", "--abbrev-ref",
            "--symbolic-full-name", "@{u}",
        ],
        capture_output=True,
        check=False,
    )
    if upstream.returncode != 0:
        remote = _detect_push_remote()
        if remote is None:
            logger.warning("No remote configured — skipping push and PR creation.")
            return False
        push_result = subprocess.run(
            ["git", "push", "-u", remote, current_branch],
            capture_output=True,
            check=False,
        )
        if push_result.returncode != 0:
            logger.error("Failed to push branch %s to %s", current_branch, remote)
            return False

    body = (
        f"## Refactoring: {scope} scope\n\n"
        f"Auto-generated by `refactor-suggest`.\n\n"
        f"- **Scope**: {scope}\n"
        f"- **Iterations**: {max_loop}\n"
        f"- **Final status**: {final_status}"
    )

    try:
        result = subprocess.run(
            [
                "gh", "pr", "create", "--draft",
                "--title",
                f"refactor({scope}): AI-suggested {scope}-level improvements",
                "--body", body,
                "--base", target_branch,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("gh CLI not found — skipping draft PR creation.")
        return False

    if result.returncode == 0:
        logger.info("Draft PR created.")
        return True

    logger.warning("Failed to create draft PR: %s", result.stderr)
    return False


def _make_plan_confirm(scope: str) -> PreFixConfirmFn:
    """Return a confirmation callback for layer/full refactor plans."""

    def _confirm(review_data: dict[str, object]) -> bool:
        plan = review_data.get("refactoring_plan")
        if isinstance(plan, dict):
            print()
            print("  Refactoring plan:")
            print(f"  Summary: {plan.get('summary', 'N/A')}")
            print(f"  Blast radius: {plan.get('estimated_blast_radius', 'N/A')}")
            steps = plan.get("steps", [])
            if isinstance(steps, list):
                print("  Steps:")
                for step in steps:
                    if isinstance(step, dict):
                        order = step.get("order", "?")
                        desc = step.get("description", "")
                        files = step.get("files", [])
                        files_str = ", ".join(str(f) for f in files) if isinstance(files, list) else ""
                        print(f"    {order}. {desc} [{files_str}]")
            print()

        try:
            answer = input(f"  Apply {scope} refactor? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        return answer == "y"

    return _confirm


# ── Main entry point ─────────────────────────────────────────────────


def run(config: LoopConfig, scope: str, *, create_pr: bool = False) -> int:
    """Run refactor-suggest and return an exit code."""
    # Restore saved scope on resume
    if config.resume:
        saved_scope_file = config.log_dir / "scope.txt"
        if saved_scope_file.is_file():
            saved_scope = saved_scope_file.read_text().strip()
            if scope not in ("auto", saved_scope):
                logger.error(
                    "Scope mismatch: --scope '%s' differs from saved scope '%s'.",
                    scope, saved_scope,
                )
                return 1
            scope = saved_scope

    # Resolve auto scope
    if scope == "auto":
        if config.dry_run:
            tools = ["codex"]
        else:
            # Fixer always uses Claude; add Codex only when it is the reviewer
            tools = ["claude"]
            if config.reviewer_backend == "codex":
                tools.append("codex")
        resolved = resolve_auto_scope(tools=tools)
        if resolved is None:
            logger.error("Budget too low for any refactor scope.")
            return 1
        logger.info("Auto scope resolved to: %s", resolved)
        scope = resolved

    # Persist resolved scope for resume
    config.scope = scope

    # Guard: non-dry-run resume requires a refactor/* branch
    if config.resume and not config.dry_run:
        if not config.current_branch.startswith("refactor/"):
            logger.error(
                "Non-dry-run resume requires a refactor/* branch, got '%s'.",
                config.current_branch,
            )
            return 1

    # Reject non-allowlisted dirty files before creating a branch
    if not config.dry_run and not config.resume:
        allowlisted = set(_ALLOWLISTED_FILES)
        non_allowed = [f for f in git_all_dirty(None) if f not in allowlisted]
        if non_allowed:
            logger.error(
                "Working tree is not clean. Commit or stash your changes "
                "before running refactor-suggest.\n  Dirty files: %s",
                ", ".join(non_allowed),
            )
            return 1

    # Create refactor branch (unless dry-run or resume)
    if not config.dry_run and not config.resume:
        branch = create_refactor_branch(scope, config.target_branch)
        if branch is None:
            return 1
        config.current_branch = branch

    # First iteration may have no diff (branch just created, or resume before
    # first commit).  Skip the no-diff early-exit in that case.
    config.skip_initial_no_diff = True

    # Collect source files
    config.log_dir.mkdir(parents=True, exist_ok=True)
    source_files_path = config.log_dir / "source-files.txt"
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=False,
    )
    source_files_path.write_text(result.stdout)
    count = len(result.stdout.strip().splitlines())
    logger.info("Collected %d source files.", count)

    reviewer = create_review_agent(config, scope=scope)
    fixer = create_fix_agent(config, variant="refactor")
    self_reviewer = (
        create_self_review_agent(config, fixer)
        if config.max_subloop > 0
        else None
    )

    # Safety confirmation for high-blast-radius scopes
    confirm_fn = None
    if scope in ("layer", "full") and not config.auto_approve:
        confirm_fn = _make_plan_confirm(scope)

    loop_result = review_fix_loop(
        config,
        reviewer=reviewer,
        fixer=fixer,
        self_reviewer=self_reviewer,
        pre_fix_confirm=confirm_fn,
        commit_pattern=f"refactor(ai-{scope}): apply iteration",
    )

    # Create draft PR when requested
    pr_failed = False
    if create_pr and not config.dry_run and loop_result.final_status in {
        FinalStatus.MAX_ITERATIONS_REACHED,
        FinalStatus.ALL_CLEAR,
    }:
        if not create_draft_pr(
            scope=scope,
            target_branch=config.target_branch,
            current_branch=config.current_branch,
            max_loop=config.max_loop,
            final_status=loop_result.final_status,
        ):
            logger.error("--create-pr was requested but PR creation failed.")
            pr_failed = True

    logger.info("Done. Status: %s", loop_result.final_status)

    success = {
        FinalStatus.ALL_CLEAR,
        FinalStatus.DRY_RUN,
        FinalStatus.MAX_ITERATIONS_REACHED,
    }
    if loop_result.final_status not in success or pr_failed:
        return 1
    return 0
