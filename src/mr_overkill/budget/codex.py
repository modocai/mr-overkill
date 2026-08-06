"""Codex CLI token-budget checker.

Ports the five functions from ``check-codex-limit.sh``:
find_latest_token_count, check_token_budget, codex_budget_sufficient.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mr_overkill.budget import (
    FIVE_HOUR_WINDOW,
    SEVEN_DAY_WINDOW,
    budget_sufficient,
    codex_parse_window,
    codex_window_kind,
)
from mr_overkill.models import BudgetScope, BudgetStatus

logger = logging.getLogger(__name__)

# Codex auth modes, as written to ``auth.json`` by ``codex login``.
AUTH_MODE_APIKEY = "apikey"
AUTH_MODE_CHATGPT = "chatgpt"
AUTH_MODE_UNKNOWN = "unknown"

# ``config.toml`` keys that record the login method Codex was set up with.
# Only ``forced_login_method`` is read: the older ``preferred_auth_method`` is
# gone from current Codex, which silently ignores it, so a stale copy left in a
# config would misreport the login Codex actually uses.
_CONFIG_AUTH_KEYS = ("forced_login_method",)

# Login-method spellings seen across auth.json and config.toml, mapped to the
# canonical mode the budget gate compares against.  ``api`` is the only value
# current Codex accepts for ``forced_login_method`` — it rejects the ``apikey``
# spellings outright — while auth.json writes ``apikey``.
_AUTH_MODE_ALIASES = {
    "api": AUTH_MODE_APIKEY,
    "apikey": AUTH_MODE_APIKEY,
    "api_key": AUTH_MODE_APIKEY,
    "api-key": AUTH_MODE_APIKEY,
    "chatgpt": AUTH_MODE_CHATGPT,
}

# Prefixes of ``codex login status`` output, mapped to the canonical mode.
# Anything else (access tokens, "Not logged in") stays ``unknown`` so the
# plan-based gate keeps running.
_LOGIN_STATUS_MODES = (
    ("logged in using an api key", AUTH_MODE_APIKEY),
    ("logged in using amazon bedrock api key", AUTH_MODE_APIKEY),
    ("logged in using chatgpt", AUTH_MODE_CHATGPT),
)


def codex_home() -> Path:
    """Return the Codex config directory (``$CODEX_HOME`` or ``~/.codex``)."""
    raw = os.environ.get("CODEX_HOME", "").strip()
    return Path(raw) if raw else Path.home() / ".codex"


def _config_auth_mode(home: Path) -> str | None:
    """Return the login method declared in ``config.toml``, or ``None``.

    Only recognised spellings are reported, so ``None`` means "the config
    declares no login method we know" — which is what lets the caller tell an
    explicit ChatGPT setup apart from a config that says nothing at all.
    """
    try:
        with (home / "config.toml").open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    for key in _CONFIG_AUTH_KEYS:
        value = config.get(key)
        if isinstance(value, str):
            mode = _AUTH_MODE_ALIASES.get(value.strip().lower())
            if mode is not None:
                return mode

    return None


def _login_status_auth_mode(home: Path) -> str | None:
    """Return the mode reported by ``codex login status``, or ``None``.

    This is the only way to read a keyring-backed login: the credential never
    touches disk, so neither auth.json nor config.toml has to mention it.
    ``None`` means the probe told us nothing — Codex is missing from PATH, it
    failed, or it named a login we deliberately do not treat as API-key auth.
    """
    try:
        result = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "CODEX_HOME": str(home)},
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None

    # Codex prints the status line on stderr; stdout is checked too so a later
    # version moving it there keeps working.  Every line is examined because a
    # warning or banner ahead of the status line must not hide it.
    for stream in (result.stderr, result.stdout):
        for line in stream.splitlines():
            status = line.strip().lower()
            for prefix, mode in _LOGIN_STATUS_MODES:
                if status.startswith(prefix):
                    return mode

    return None


def detect_auth_mode(home: Path | None = None) -> str:
    """Detect how the Codex CLI authenticates.

    ``CODEX_API_KEY`` wins outright: Codex sends it even when ``auth.json``
    holds a ChatGPT login.  Otherwise ``auth.json`` is authoritative, since it
    records the mode chosen by the last ``codex login``.  Falls back to the
    login method declared in ``config.toml``, then to what ``codex login
    status`` reports, then to ``unknown`` (which keeps the plan-based budget
    gate active).
    """
    if home is None:
        home = codex_home()

    # Checked before auth.json: Codex authenticates with this key regardless of
    # what the last login stored.
    if os.environ.get("CODEX_API_KEY", "").strip():
        return AUTH_MODE_APIKEY

    try:
        auth = json.loads((home / "auth.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        auth = None

    if isinstance(auth, dict):
        mode = auth.get("auth_mode")
        if isinstance(mode, str) and mode.strip():
            # Unrecognised spellings pass through as-is: they are not the
            # API-key mode the gate bypasses, so the gate stays active.
            spelling = mode.strip().lower()
            return _AUTH_MODE_ALIASES.get(spelling, spelling)
        # Older Codex versions omit auth_mode; infer from the stored payload.
        if auth.get("tokens"):
            return AUTH_MODE_CHATGPT
        if auth.get("OPENAI_API_KEY"):
            return AUTH_MODE_APIKEY

    # auth.json can be missing entirely when Codex keeps credentials in the OS
    # keyring (``cli_auth_credentials_store``); config.toml may still declare
    # the login method, so consult it before giving up on the file layout.
    config_mode = _config_auth_mode(home)
    if config_mode is not None:
        return config_mode

    # Nothing on disk names the login, which is normal for a keyring-backed
    # one.  Ask Codex itself as a last resort.  An ambient OPENAI_API_KEY is
    # deliberately not consulted: Codex does not authenticate with it, so a key
    # left in the environment for other tools must not switch the gate off.
    status_mode = _login_status_auth_mode(home)
    if status_mode is not None:
        return status_mode

    return AUTH_MODE_UNKNOWN


def find_latest_token_count(
    sessions_dir: Path | None = None,
) -> dict[str, object] | None:
    """Scan Codex session logs from last 7 days for the latest token_count event.

    Returns the event dict, or ``None`` if no data found.
    """
    if sessions_dir is None:
        sessions_dir = codex_home() / "sessions"

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


def check_token_budget(
    sessions_dir: Path | None = None,
    home: Path | None = None,
) -> BudgetStatus:
    """Get Codex budget status from session logs.

    API-key auth is billed per token and carries no plan rate-limit windows,
    so no budget data is reported for it — session logs written under a
    previous ChatGPT login would otherwise gate the loop indefinitely.
    """
    if home is None:
        # Session logs live at <codex_home>/sessions; callers that override
        # only sessions_dir (tests, custom layouts) get the matching home.
        home = sessions_dir.parent if sessions_dir is not None else codex_home()

    auth_mode = detect_auth_mode(home)
    if auth_mode == AUTH_MODE_APIKEY:
        logger.info(
            "Codex is on API-key auth — no plan rate limits to check.",
        )
        return BudgetStatus(
            five_hour_used_pct=None,
            seven_day_used_pct=None,
            tokens_used=0,
            mode=AUTH_MODE_APIKEY,
            tier="",
            resets_at=None,
            seven_day_resets_at=None,
        )

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

    windows = _map_windows(
        (
            (primary, FIVE_HOUR_WINDOW),
            (secondary, SEVEN_DAY_WINDOW),
        ),
        now_epoch,
    )
    five_pct, five_resets = windows.get(FIVE_HOUR_WINDOW, (None, None))
    seven_pct, seven_resets = windows.get(SEVEN_DAY_WINDOW, (None, None))

    return BudgetStatus(
        five_hour_used_pct=five_pct,
        seven_day_used_pct=seven_pct,
        tokens_used=0,
        mode="session_log",
        tier="",
        resets_at=five_resets,
        seven_day_resets_at=seven_resets,
    )


def _map_windows(
    slots: tuple[tuple[object, str], ...],
    now_epoch: int,
) -> dict[str, tuple[int | None, str | None]]:
    """Assign Codex rate-limit windows to their BudgetStatus fields.

    Each ``(window, positional_kind)`` pair is classified by the window's own
    ``window_minutes``; the positional kind is only a fallback for payloads
    that omit it.  When two windows land on the same kind, the higher usage
    wins so the gate stays conservative.
    """
    mapped: dict[str, tuple[int | None, str | None]] = {}

    for window, positional_kind in slots:
        if not isinstance(window, dict) or not window:
            continue

        kind = codex_window_kind(window) or positional_kind
        pct, resets = codex_parse_window(window, now_epoch)

        previous = mapped.get(kind)
        if previous is not None:
            prev_pct = previous[0]
            if pct is None or (prev_pct is not None and prev_pct >= pct):
                continue

        mapped[kind] = (pct, resets)

    return mapped


def codex_budget_sufficient(
    scope: BudgetScope,
    status: BudgetStatus | None = None,
    sessions_dir: Path | None = None,
) -> bool:
    """Go/no-go for Codex at the given scope."""
    if status is None:
        status = check_token_budget(sessions_dir)
    if status.mode == AUTH_MODE_APIKEY:
        return True
    return budget_sufficient(scope, status)
