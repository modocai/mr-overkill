"""Budget go/no-go policy and Codex window parsing.

Ports ``_budget_sufficient`` from ``common.sh`` and
``_codex_limit_parse_window`` from ``check-codex-limit.sh``.
"""

from __future__ import annotations

import logging

from mr_overkill.models import BudgetScope, BudgetStatus
from mr_overkill.time_utils import codex_ts_to_iso

logger = logging.getLogger(__name__)

# Thresholds: scope → max allowed 5-hour percentage (exclusive).
_THRESHOLDS: dict[BudgetScope, int | None] = {
    BudgetScope.MICRO: 90,
    BudgetScope.MODULE: 75,
    BudgetScope.LAYER: None,  # no threshold established
    BudgetScope.FULL: None,
}


def has_threshold(scope: BudgetScope) -> bool:
    """Return True if *scope* has a defined go/no-go threshold."""
    return _THRESHOLDS.get(scope) is not None


def budget_sufficient(scope: BudgetScope, status: BudgetStatus) -> bool:
    """Go/no-go decision based on usage thresholds.

    Thresholds: micro < 90 %, module < 75 %, layer/full = always go.
    7-day guard: >= 100 % always no-go, >= 90 % no-go for module+.
    """
    threshold = _THRESHOLDS.get(scope)
    if threshold is None:
        logger.warning("No established threshold for '%s' — assuming OK", scope)
        return True

    # 7-day window guard
    pct_7d = status.seven_day_used_pct
    if pct_7d is not None:
        if pct_7d >= 100:
            logger.warning(
                "Budget check failed: 7-day window %d%% used (exhausted)", pct_7d
            )
            return False
        if pct_7d >= 90 and threshold <= 75:
            logger.warning(
                "Budget check failed: 7-day window %d%% used "
                "(threshold for '%s' requires <90%%)",
                pct_7d,
                scope,
            )
            return False

    # 5-hour window check
    pct = status.five_hour_used_pct
    if pct is None:
        logger.info("No budget data — assuming OK (first run or stale logs)")
        return True

    if pct < threshold:
        return True

    logger.warning(
        "Budget check failed: %d%% used (threshold for '%s' is <%d%%)",
        pct,
        scope,
        threshold,
    )
    return False


def codex_parse_window(
    window: dict[str, object] | None,
    now_epoch: int,
    default_pct: int | None = None,
) -> tuple[int | None, str | None]:
    """Parse a Codex rate_limits window into ``(used_pct, resets_at_iso)``.

    Returns ``(None, None)`` if *window* is ``None`` or empty.
    Returns ``(0, None)`` if the window has expired.
    """
    if not window:
        return default_pct, None

    resets_at = window.get("resets_at")

    # Window expired?
    if (
        resets_at is not None
        and isinstance(resets_at, int | float)
        and int(resets_at) <= now_epoch
    ):
        return 0, None

    used_pct_raw = window.get("used_percent")
    if used_pct_raw is None:
        return default_pct, None

    pct = round(float(str(used_pct_raw)))

    resets_iso: str | None = None
    if isinstance(resets_at, int | float):
        resets_iso = codex_ts_to_iso(int(resets_at))

    return pct, resets_iso
