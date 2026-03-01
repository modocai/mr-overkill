"""Codex CLI token-budget checker.

Ports the five functions from ``check-codex-limit.sh``:
find_latest_token_count, check_token_budget, codex_budget_sufficient.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mr_overkill.budget import budget_sufficient, codex_parse_window
from mr_overkill.models import BudgetScope, BudgetStatus

logger = logging.getLogger(__name__)


def find_latest_token_count(
    sessions_dir: Path | None = None,
) -> dict[str, object] | None:
    """Scan Codex session logs from last 7 days for the latest token_count event.

    Returns the event dict, or ``None`` if no data found.
    """
    if sessions_dir is None:
        sessions_dir = Path.home() / ".codex" / "sessions"

    if not sessions_dir.is_dir():
        return None

    best_event: dict[str, object] | None = None
    best_ts: object = None

    now = datetime.now(tz=UTC)
    for offset in range(7):
        day = now - timedelta(days=offset)
        day_dir = sessions_dir / day.strftime("%Y/%m/%d")
        if not day_dir.is_dir():
            continue

        for jsonl_file in day_dir.glob("*.jsonl"):
            try:
                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if (
                        entry.get("type") == "event_msg"
                        and isinstance(entry.get("payload"), dict)
                        and entry["payload"].get("type") == "token_count"
                    ):
                        ts = entry.get("timestamp")
                        if best_ts is None or (ts is not None and ts > best_ts):
                            best_ts = ts
                            best_event = entry
            except OSError:
                continue

    return best_event


def check_token_budget(sessions_dir: Path | None = None) -> BudgetStatus:
    """Get Codex budget status from session logs."""
    event = find_latest_token_count(sessions_dir)

    if event is None:
        return BudgetStatus(
            five_hour_used_pct=None,
            seven_day_used_pct=None,
            tokens_used=0,
            mode="no_data",
            tier="",
            resets_at=None,
            seven_day_resets_at=None,
        )

    now_epoch = int(datetime.now(tz=UTC).timestamp())
    payload = event.get("payload", {})
    rate_limits = payload.get("rate_limits", {}) if isinstance(payload, dict) else {}

    primary = rate_limits.get("primary") if isinstance(rate_limits, dict) else None
    secondary = rate_limits.get("secondary") if isinstance(rate_limits, dict) else None

    five_pct, five_resets = codex_parse_window(
        primary if isinstance(primary, dict) else None,
        now_epoch,
    )
    seven_pct, seven_resets = codex_parse_window(
        secondary if isinstance(secondary, dict) else None,
        now_epoch,
    )

    return BudgetStatus(
        five_hour_used_pct=five_pct,
        seven_day_used_pct=seven_pct,
        tokens_used=0,
        mode="session_log",
        tier="",
        resets_at=five_resets,
        seven_day_resets_at=seven_resets,
    )


def codex_budget_sufficient(
    scope: BudgetScope,
    status: BudgetStatus | None = None,
    sessions_dir: Path | None = None,
) -> bool:
    """Go/no-go for Codex at the given scope."""
    if status is None:
        status = check_token_budget(sessions_dir)
    return budget_sufficient(scope, status)
