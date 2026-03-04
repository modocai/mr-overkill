"""Review-loop entry point — wires Protocol implementations to loop_engine.

Ports the argument parsing and orchestration from ``bin/review-loop.sh``,
delegating the actual loop to :func:`loop_engine.review_fix_loop`.
"""

from __future__ import annotations

import json
import logging
import string
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mr_overkill.budget.claude import claude_budget_sufficient
from mr_overkill.budget.codex import codex_budget_sufficient
from mr_overkill.loop_engine import ReviewerFn, review_fix_loop
from mr_overkill.models import (
    BudgetScope,
    BudgetTimeoutError,
    FinalStatus,
    LoopConfig,
    WorktreeSnapshot,
)
from mr_overkill.retry import (
    retry_claude_cmd,
    retry_codex_cmd,
    wait_for_budget,
)
from mr_overkill.self_review import self_review_subloop
from mr_overkill.two_step_fix import claude_two_step_fix

logger = logging.getLogger(__name__)


# ── Budget helper ────────────────────────────────────────────────────


def _budget_check(tool: str, scope: BudgetScope, max_wait: int) -> bool:
    """Direct budget check without waiting (for wait_for_budget's inner fn)."""
    if tool == "claude":
        return claude_budget_sufficient(scope)
    if tool == "codex":
        return codex_budget_sufficient(scope)
    return True


def _wait_budget(
    tool: str, scope: BudgetScope, max_wait: int, default_wait: int
) -> bool:
    """Wait for budget using the polling loop."""
    actual = max_wait if max_wait > 0 else default_wait
    return wait_for_budget(
        _budget_check, tool, scope, actual
    )


# ── Retry helper ─────────────────────────────────────────────────────


def _make_retry_fn(
    config: LoopConfig,
) -> Callable[..., bool]:
    """Create a retry function bound to config settings."""

    def retry_fn(
        output_path: Path,
        label: str,
        cmd_args: list[str],
        **kw: Any,
    ) -> bool:
        return retry_claude_cmd(
            output_path,
            label,
            cmd_args,
            stdin=kw.get("stdin"),
            max_wait=config.retry_max_wait,
            initial_wait=config.retry_initial_wait,
            diagnostic_log=config.diagnostic_log,
        )

    return retry_fn


# ── Protocol-conforming wrappers ─────────────────────────────────────


def _make_reviewer(
    config: LoopConfig,
) -> ReviewerFn:
    """Create a ReviewerFn that runs Codex review."""

    def reviewer(output_path: Path, iteration: int) -> bool:
        prompt_file = config.prompts_dir / "codex-review.prompt.md"
        if not prompt_file.is_file():
            logger.error("Review prompt not found: %s", prompt_file)
            return False

        tmpl = string.Template(
            prompt_file.read_text(encoding="utf-8")
        )
        prompt_text = tmpl.safe_substitute({
            "CURRENT_BRANCH": config.current_branch,
            "TARGET_BRANCH": config.target_branch,
            "ITERATION": str(iteration),
        })

        # Pre-flight budget check
        if not _wait_budget(
            "codex", config.budget_scope, 0, config.retry_max_wait
        ):
            raise BudgetTimeoutError(
                f"Codex budget timeout (iteration {iteration})."
            )

        stderr_path = output_path.with_suffix(".stderr")
        return retry_codex_cmd(
            stderr_path,
            "Codex review",
            [
                "codex", "exec", "--sandbox", "read-only",
                "-o", str(output_path), prompt_text,
            ],
            max_wait=config.retry_max_wait,
            initial_wait=config.retry_initial_wait,
        )

    return reviewer


def _make_fixer(
    config: LoopConfig,
) -> Callable[..., bool]:
    """Create a FixFn that runs two-step Claude fix."""
    retry_fn = _make_retry_fn(config)

    def budget_fn(
        tool: str, scope: BudgetScope, max_wait: int
    ) -> bool:
        return _wait_budget(tool, scope, max_wait, config.retry_max_wait)

    def fixer(review_json: str, label: str, **kw: Any) -> bool:
        log_dir = config.log_dir
        opinion_file = log_dir / f"opinion-{label}.md"
        fix_file = log_dir / f"fix-{label}.md"

        return claude_two_step_fix(
            review_json=review_json,
            opinion_file=opinion_file,
            fix_file=fix_file,
            label=label,
            retry_fn=retry_fn,
            budget_fn=budget_fn,
            prompts_dir=config.prompts_dir,
            current_branch=config.current_branch,
            target_branch=config.target_branch,
            budget_scope=config.budget_scope,
            budget_max_wait=config.retry_max_wait,
            fix_history=kw.get("fix_history", ""),
        )

    return fixer


def _make_self_reviewer(
    config: LoopConfig,
    fixer: Callable[..., bool] | None = None,
) -> Callable[..., str]:
    """Create a SelfReviewFn wrapping self_review_subloop."""
    retry_fn = _make_retry_fn(config)

    def budget_fn(
        tool: str, scope: BudgetScope, max_wait: int
    ) -> bool:
        return _wait_budget(tool, scope, max_wait, config.retry_max_wait)

    if fixer is None:
        fixer = _make_fixer(config)

    def self_reviewer(
        pre_fix_snapshot: list[WorktreeSnapshot],
        max_subloop: int,
        log_dir: Path,
        iteration: int,
        review_json_str: str,
    ) -> str:
        def fix_fn(
            review_json: str, label: str, **kw: Any
        ) -> bool:
            return bool(fixer(review_json, label, **kw))

        return self_review_subloop(
            pre_fix_snapshot=pre_fix_snapshot,
            max_subloop=max_subloop,
            log_dir=log_dir,
            iteration=iteration,
            review_json_str=review_json_str,
            retry_fn=retry_fn,
            budget_fn=budget_fn,
            fix_fn=fix_fn,
            prompts_dir=config.prompts_dir,
            current_branch=config.current_branch,
            target_branch=config.target_branch,
            budget_scope=config.budget_scope,
            dry_run=config.dry_run,
            fix_nits=config.fix_nits,
            original_review_json=json.loads(review_json_str),
        )

    return self_reviewer


# ── Main entry point ─────────────────────────────────────────────────


def run(config: LoopConfig) -> int:
    """Run the review loop and return an exit code (0 = success)."""
    result = review_fix_loop(
        config,
        reviewer=_make_reviewer(config),
        fixer=_make_fixer(config),
        self_reviewer=(
            _make_self_reviewer(config)
            if config.max_subloop > 0
            else None
        ),
    )

    logger.info("Done. Status: %s", result.final_status)
    if result.summary_path:
        logger.info("Summary: %s", result.summary_path)

    success_statuses = {
        FinalStatus.ALL_CLEAR,
        FinalStatus.DRY_RUN,
        FinalStatus.AUTO_COMMIT_DISABLED,
        FinalStatus.NO_DIFF,
        FinalStatus.MAX_ITERATIONS_REACHED,
    }
    return 0 if result.final_status in success_statuses else 1
