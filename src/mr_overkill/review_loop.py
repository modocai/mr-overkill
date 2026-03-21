"""Review-loop entry point — wires Protocol implementations to loop_engine.

Wires Protocol implementations to loop_engine for the review-fix loop.
"""

from __future__ import annotations

import logging

from mr_overkill.agents import (
    create_fix_agent,
    create_review_agent,
    create_self_review_agent,
)
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

    success_statuses = {
        FinalStatus.ALL_CLEAR,
        FinalStatus.DRY_RUN,
        FinalStatus.AUTO_COMMIT_DISABLED,
        FinalStatus.NO_DIFF,
        FinalStatus.MAX_ITERATIONS_REACHED,
    }
    return 0 if result.final_status in success_statuses else 1
