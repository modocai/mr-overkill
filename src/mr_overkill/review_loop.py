"""Review-loop entry point — wires Protocol implementations to loop_engine."""

from __future__ import annotations

import logging

from mr_overkill import commit_scope
from mr_overkill.agents import (
    create_fix_agent,
    create_review_agent,
    create_self_review_agent,
)
from mr_overkill.git_ops import push_trigger_commit
from mr_overkill.loop_engine import _reject_dirty_worktree, review_fix_loop
from mr_overkill.models import (
    FinalStatus,
    LoopConfig,
)

logger = logging.getLogger(__name__)


# ── Main entry point ─────────────────────────────────────────────────


def _prepare_commit_scope(config: LoopConfig) -> bool:
    """Materialise the scope diff and create the review branch.

    Mirrors ``refactor_suggest.run``'s preamble: guard, capture the scope
    artefact, create a work branch, then let the loop run unchanged.
    Returns False if the run cannot proceed.
    """
    sha = config.scope_commit
    if sha is None:
        # Not a commit-scope run: drop any scope.diff a previous one left
        # behind, so a stale file can never be mistaken for this run's scope.
        (config.log_dir / "scope.diff").unlink(missing_ok=True)
        return True

    if (
        config.resume
        and not config.dry_run
        and not config.current_branch.startswith("review/")
    ):
        logger.error(
            "Non-dry-run resume of a commit-scope run requires a review/* branch, "
            "got '%s'.",
            config.current_branch,
        )
        return False

    if not config.dry_run:
        non_allowed = _reject_dirty_worktree(None)
        if non_allowed:
            logger.error(
                "Working tree is not clean. Commit or stash your changes "
                "before running review-loop.\n  Dirty files: %s",
                ", ".join(non_allowed),
            )
            return False

    config.log_dir.mkdir(parents=True, exist_ok=True)
    assert config.scope_diff_file is not None
    size = commit_scope.write_scope_diff(sha, config.scope_diff_file)
    if size == 0:
        logger.error(
            "Commit %s produces an empty diff — nothing to review.", sha[:7]
        )
        return False

    logger.info("Commit scope: %s", commit_scope.commit_headline(sha))
    if commit_scope.is_merge_commit(sha):
        logger.info("Merge commit — diffed against its first parent.")
    if not commit_scope.is_ancestor_of_head(sha):
        logger.warning(
            "%s is not an ancestor of HEAD — some of the code it touched "
            "may not exist in this working tree.",
            sha[:7],
        )
    logger.info("Scope diff: %s (%d bytes)", config.scope_diff_file, size)

    if not config.dry_run and not config.resume:
        branch = commit_scope.review_branch_name(sha)
        if not commit_scope.create_branch_at_head(branch):
            return False
        config.current_branch = branch

    # Iteration 1 has no branch diff yet: the work branch was just created.
    config.skip_initial_no_diff = True

    if config.dry_run:
        logger.info("Dry-run — no branch created, no fixes applied.")
    else:
        published = "will be pushed" if config.push_branch else "local only"
        logger.info(
            "Fixes will land on %s (%s). No PR will be created.",
            config.current_branch,
            published,
        )
    return True


def run(config: LoopConfig) -> int:
    """Run the review loop and return an exit code (0 = success)."""
    if not _prepare_commit_scope(config):
        return 1

    reviewer = create_review_agent(config)
    fixer = create_fix_agent(config)
    self_reviewer = (
        create_self_review_agent(config, fixer)
        if config.max_subloop > 0
        else None
    )

    result = review_fix_loop(
        config,
        reviewer=reviewer,
        fixer=fixer,
        self_reviewer=self_reviewer,
    )

    logger.info("Done. Status: %s", result.final_status)
    if result.summary_path:
        logger.info("Summary: %s", result.summary_path)

    if (
        result.final_status == FinalStatus.ALL_CLEAR
        and config.ci_trigger_mode == "last-only"
        and config.auto_commit
        and not config.dry_run
        and not config.scope_commit
        and result.made_skipped_fix_commit
    ):
        try:
            push_trigger_commit(branch=config.current_branch)
        except RuntimeError as exc:
            logger.warning("Could not push CI trigger commit: %s", exc)

    if config.scope_commit and not config.dry_run:
        logger.info(
            "Fixes are on %s. Push and open a PR when ready.",
            config.current_branch,
        )

    success_statuses = {
        FinalStatus.ALL_CLEAR,
        FinalStatus.DRY_RUN,
        FinalStatus.AUTO_COMMIT_DISABLED,
        FinalStatus.NO_DIFF,
        FinalStatus.MAX_ITERATIONS_REACHED,
    }
    return 0 if result.final_status in success_statuses else 1
