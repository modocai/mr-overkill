"""Budget go/no-go policy and Codex window parsing.

Ports ``_budget_sufficient`` from ``common.sh`` and
``_codex_limit_parse_window`` from ``check-codex-limit.sh``.
"""

from __future__ import annotations

import logging
import os

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

# Rate-limit window kinds, keyed by the field they map onto in BudgetStatus.
FIVE_HOUR_WINDOW = "five_hour"
SEVEN_DAY_WINDOW = "seven_day"

# Any window declared as one day or shorter counts as the short ("5-hour")
# window; anything longer is the rolling weekly window.
_SHORT_WINDOW_MAX_MINUTES = 24 * 60

# Env escape hatch: set to 1/true/yes/on to bypass every budget gate.
SKIP_BUDGET_ENV_VAR = "OVERKILL_SKIP_BUDGET"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def budget_gate_disabled() -> bool:
    """Return True if the user has disabled budget gating via the environment.

    Local budget data is an estimate derived from CLI logs; when it is wrong
    (stale logs, changed auth mode, new rate-limit shapes) the loop would
    otherwise block with no way out.
    """
    return os.environ.get(SKIP_BUDGET_ENV_VAR, "").strip().lower() in _TRUTHY


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
        if pct_7d >= 90 and scope != BudgetScope.MICRO:
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


def codex_window_kind(window: dict[str, object] | None) -> str | None:
    """Classify a Codex ``rate_limits`` window by its declared length.

    Codex does not guarantee that ``primary`` is the short window — on some
    plans ``primary`` carries the weekly (``window_minutes: 10080``) limit.
    Returns ``None`` when the window omits ``window_minutes``, leaving the
    caller to fall back to positional assignment.
    """
    if not window:
        return None

    raw = window.get("window_minutes")
    if raw is None:
        return None

    try:
        minutes = float(str(raw))
    except ValueError:
        return None

    if minutes <= 0:
        return None

    return (
        FIVE_HOUR_WINDOW
        if minutes <= _SHORT_WINDOW_MAX_MINUTES
        else SEVEN_DAY_WINDOW
    )


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
