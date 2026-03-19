"""Gemini budget checker — stub returning always-sufficient.

Gemini uses API-key-based billing, so there is no local session/OAuth
budget mechanism to query.  This stub always returns True so that the
existing budget-gate pipeline works without special-casing.
"""

from __future__ import annotations

import logging

from mr_overkill.models import BudgetScope

logger = logging.getLogger(__name__)


def gemini_budget_sufficient(scope: BudgetScope) -> bool:
    """Go/no-go for Gemini — always returns True (no local budget data)."""
    logger.info(
        "Gemini budget check (scope=%s): no local budget data — assuming OK.",
        scope,
    )
    return True
