"""Time-related utilities for retry and budget logic.

Ports ``_seconds_until_iso`` (retry.sh) and ``_codex_limit_ts_to_iso``
(check-codex-limit.sh) to Python.
"""

from __future__ import annotations

from datetime import UTC, datetime


def seconds_until_iso(iso_ts: str) -> int:
    """Return seconds remaining until an ISO 8601 timestamp (0 if past).

    Handles common ISO 8601 variants: trailing ``Z``, ``+00:00``, and
    fractional seconds.  Returns ``0`` for invalid or past timestamps.
    """
    try:
        # Python 3.11+ fromisoformat handles Z and +00:00 natively.
        target = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return 0

    # Ensure the target is timezone-aware (assume UTC if naive).
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)

    diff = int((target - datetime.now(tz=UTC)).total_seconds())
    return max(diff, 0)


def codex_ts_to_iso(unix_ts: int) -> str:
    """Convert a Unix epoch timestamp to an ISO 8601 UTC string.

    Format: ``YYYY-MM-DDTHH:MM:SSZ`` (no fractional seconds).
    """
    dt = datetime.fromtimestamp(unix_ts, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
