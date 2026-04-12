"""Claude Code token-budget checker.

Ports the five functions from ``check-claude-limit.sh``:
detect_tier, check_oauth, check_local, check_token_budget,
claude_budget_sufficient.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mr_overkill.budget import budget_sufficient
from mr_overkill.models import BudgetScope, BudgetStatus

logger = logging.getLogger(__name__)

def _sum_usage(usage: dict[str, int]) -> float:
    """Sum token usage with approximate rate-limit weights.

    Anthropic rate limits weight cache tokens differently:
    cache_creation ~0.25x, cache_read ~0.08x of full token cost.
    Returns float to avoid per-entry truncation; caller should round once.
    """
    return (
        usage.get("input_tokens", 0)
        + usage.get("output_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0) * 0.25
        + usage.get("cache_read_input_tokens", 0) * 0.08
    )


# Approximate 5-hour token limits per tier.
# Derived from comparing local weighted-token sums against OAuth utilization
# percentages (e.g. 5.4M weighted tokens ≈ 60% → ~9-10M limit for max5).
# These are best-effort estimates; the authoritative value comes from OAuth.
_TIER_LIMITS: dict[str, int] = {
    "pro": 2_000_000,
    "max5": 10_000_000,
    "max20": 40_000_000,
}

_TIER_MAP: dict[str, str] = {
    "default_claude_max_20x": "max20",
    "default_claude_max_5x": "max5",
}


def detect_tier(telemetry_dir: Path | None = None) -> str:
    """Read rateLimitTier from Claude Code telemetry files.

    Returns ``'pro'``, ``'max5'``, or ``'max20'``.
    """
    if telemetry_dir is None:
        telemetry_dir = Path.home() / ".claude" / "telemetry"

    if not telemetry_dir.is_dir():
        return "pro"

    best_ts = ""
    best_tier = ""

    for f in telemetry_dir.glob("*.json"):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue

        # Try whole-file JSON first (pretty-printed), fall back to JSONL.
        try:
            entries = [json.loads(text)]
        except json.JSONDecodeError:
            entries = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        for data in entries:
            if not isinstance(data, dict):
                continue
            event_data = data.get("event_data")
            if not isinstance(event_data, dict):
                continue

            ua = event_data.get("user_attributes")
            if isinstance(ua, str):
                try:
                    ua = json.loads(ua)
                except json.JSONDecodeError:
                    continue
            if not isinstance(ua, dict):
                continue

            tier_raw = ua.get("rateLimitTier", "")
            if not tier_raw:
                continue

            ts = str(
                event_data.get("client_timestamp")
                or event_data.get("timestamp")
                or ""
            )
            if ts >= best_ts:
                best_ts = ts
                best_tier = str(tier_raw)

    return _TIER_MAP.get(best_tier, "pro")


def check_oauth() -> BudgetStatus | None:
    """Try OAuth-based budget check via macOS Keychain.

    Returns ``None`` on any failure (not macOS, no credentials, API error).
    """
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password",
                "-s", "Claude Code-credentials", "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.warning("OAuth: keychain access failed (rc=%d)", result.returncode)
            return None

        creds = json.loads(result.stdout.strip())
        token = creds.get("claudeAiOauth", {}).get("accessToken")
        if not token:
            logger.warning("OAuth: no access token in credentials")
            return None

        resp = subprocess.run(
            [
                "curl",
                "-s",
                "--max-time",
                "10",
                "https://api.anthropic.com/api/oauth/usage",
                "-H",
                f"Authorization: Bearer {token}",
                "-H",
                "anthropic-beta: oauth-2025-04-20",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if resp.returncode != 0 or not resp.stdout.strip():
            logger.warning("OAuth: API request failed (rc=%d)", resp.returncode)
            return None

        data = json.loads(resp.stdout)

        # Check for API error response (e.g. rate_limit_error)
        if "error" in data:
            err = data["error"]
            logger.warning(
                "OAuth: API error — %s: %s",
                err.get("type", "unknown"),
                err.get("message", ""),
            )
            return None

        five_hour = data.get("five_hour", {})
        utilization = five_hour.get("utilization")
        if utilization is None:
            logger.warning("OAuth: no utilization in response")
            return None

        tier = detect_tier()
        seven_day = data.get("seven_day")

        return BudgetStatus(
            five_hour_used_pct=round(utilization),
            seven_day_used_pct=round(seven_day["utilization"]) if seven_day else None,
            tokens_used=0,
            mode="oauth",
            tier=tier,
            resets_at=five_hour.get("resets_at"),
            seven_day_resets_at=seven_day.get("resets_at") if seven_day else None,
            estimated=False,
        )
    except (
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        KeyError,
        OSError,
    ) as exc:
        logger.warning("OAuth: %s", exc)
        return None


def check_local(projects_dir: Path | None = None) -> BudgetStatus:
    """Estimate budget from local JSONL session logs.

    Scans files modified in the last 5 hours, sums token usage
    (deduplicating by message.id), and calculates percentage.
    """
    tier = detect_tier()
    limit = _TIER_LIMITS.get(tier, _TIER_LIMITS["pro"])

    if projects_dir is None:
        projects_dir = Path.home() / ".claude" / "projects"

    cutoff = datetime.now(tz=UTC) - timedelta(hours=5)

    # Find JSONL files modified in the last 5 hours
    total_tokens: float = 0
    seen_ids: dict[str, dict[str, int]] = {}  # message_id → usage dict

    if projects_dir.is_dir():
        for jsonl_file in projects_dir.rglob("*.jsonl"):
            try:
                stat = jsonl_file.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                if mtime < cutoff:
                    continue

                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if entry.get("type") != "assistant":
                        continue

                    ts = entry.get("timestamp", "")
                    if ts < cutoff.strftime("%Y-%m-%dT%H:%M:%S"):
                        continue

                    message = entry.get("message", {})
                    usage = message.get("usage")
                    if not usage:
                        continue

                    msg_id = message.get("id", "")
                    if msg_id:
                        seen_ids[msg_id] = usage
                    else:
                        total_tokens += _sum_usage(usage)
            except OSError:
                continue

    # Sum deduplicated usage
    for usage in seen_ids.values():
        total_tokens += _sum_usage(usage)

    pct = (int(total_tokens) * 100 // limit) if total_tokens > 0 and limit > 0 else 0

    logger.info(
        "Local estimate: %d%% (%d weighted tokens / %d limit, "
        "tier=%s, %d deduplicated messages)",
        pct,
        int(total_tokens),
        limit,
        tier,
        len(seen_ids),
    )

    return BudgetStatus(
        five_hour_used_pct=pct,
        seven_day_used_pct=None,
        tokens_used=int(total_tokens),
        mode="local",
        tier=tier,
        resets_at=None,
        seven_day_resets_at=None,
        estimated=True,
    )


_CACHE_PATH = Path.home() / ".cache" / "mr-overkill" / "claude-budget.json"


def _save_cache(status: BudgetStatus) -> None:
    """Persist a successful OAuth result to disk."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps(dataclasses.asdict(status)), encoding="utf-8",
        )
    except OSError:
        pass


def _load_cache() -> BudgetStatus | None:
    """Load the last successful OAuth result from disk.

    Returns ``None`` if both cached windows have expired.
    """
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        now = datetime.now(tz=UTC)
        # Clear stale 5-hour data if that window has already reset.
        resets_at = data.get("resets_at")
        if not resets_at or now > datetime.fromisoformat(resets_at):
            data["five_hour_used_pct"] = None
            data["resets_at"] = None
        # Clear stale 7-day data if that window has already reset.
        seven_day_ra = data.get("seven_day_resets_at")
        if not seven_day_ra or now > datetime.fromisoformat(seven_day_ra):
            data["seven_day_used_pct"] = None
            data["seven_day_resets_at"] = None
        # Nothing useful remains.
        five_gone = data.get("five_hour_used_pct") is None
        seven_gone = data.get("seven_day_used_pct") is None
        if five_gone and seven_gone:
            return None
        data["mode"] = "cached"
        data["estimated"] = True
        for key in ("five_hour_used_pct", "seven_day_used_pct", "tokens_used"):
            val = data.get(key)
            if val is not None:
                data[key] = int(val)
        return BudgetStatus(**data)
    except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None


def check_token_budget() -> BudgetStatus:
    """Get Claude budget status.

    Priority: OAuth → max(cached OAuth, local) for 5h → local JSONL.
    """
    result = check_oauth()
    if result is not None:
        _save_cache(result)
        # Also run local estimate for calibration logging
        local = check_local()
        if local.five_hour_used_pct is not None:
            logger.info(
                "Budget calibration: oauth=%d%% local=%d%% "
                "(diff=%+dpp, %d weighted tokens, tier=%s)",
                result.five_hour_used_pct or 0,
                local.five_hour_used_pct,
                (local.five_hour_used_pct) - (result.five_hour_used_pct or 0),
                local.tokens_used,
                local.tier,
            )
        return result

    cached = _load_cache()
    local = check_local()

    if cached is not None:
        if cached.five_hour_used_pct is not None:
            # Use the higher 5h value between cached and local to avoid
            # underreporting when cache is stale.
            if (
                local.five_hour_used_pct is not None
                and local.five_hour_used_pct > cached.five_hour_used_pct
            ):
                cached = dataclasses.replace(
                    cached, five_hour_used_pct=local.five_hour_used_pct,
                )
            logger.info("Using cached OAuth budget data.")
            return cached
        # 5-hour expired but 7-day still valid — merge 7-day into local.
        logger.info(
            "Cached OAuth 5-hour expired; using local estimate "
            "with cached 7-day data.",
        )
        return dataclasses.replace(
            local,
            seven_day_used_pct=cached.seven_day_used_pct,
            seven_day_resets_at=cached.seven_day_resets_at,
        )

    return local


def claude_budget_sufficient(
    scope: BudgetScope,
    status: BudgetStatus | None = None,
) -> bool:
    """Go/no-go for Claude at the given scope."""
    if status is None:
        status = check_token_budget()
    return budget_sufficient(scope, status)
