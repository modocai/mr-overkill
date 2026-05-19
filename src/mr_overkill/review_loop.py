"""Review-loop entry point — wires Protocol implementations to loop_engine."""

from __future__ import annotations

import logging

from mr_overkill.agents import (
    create_fix_agent,
    create_review_agent,
    create_self_review_agent,
)
from mr_overkill.git_ops import push_trigger_commit
from mr_overkill.loop_engine import review_fix_loop
from mr_overkill.models import (
    FinalStatus,
    LoopConfig,
)

logger = logging.getLogger(__name__)


# ── Main entry point ─────────────────────────────────────────────────


def run(config: LoopConfig) -> int:
    """Run the review loop and return an exit code (0 = success)."""
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
        and result.made_skipped_fix_commit
    ):
        try:
            push_trigger_commit(branch=config.current_branch)
        except RuntimeError as exc:
            logger.warning("Could not push CI trigger commit: %s", exc)

    success_statuses = {
        FinalStatus.ALL_CLEAR,
        FinalStatus.DRY_RUN,
        FinalStatus.AUTO_COMMIT_DISABLED,
        FinalStatus.NO_DIFF,
        FinalStatus.MAX_ITERATIONS_REACHED,
    }
    return 0 if result.final_status in success_statuses else 1
