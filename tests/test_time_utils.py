"""Tests for mr_overkill.time_utils — ISO timestamp helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mr_overkill.time_utils import codex_ts_to_iso, seconds_until_iso

# ── seconds_until_iso ────────────────────────────────────────────────


def test_future_timestamp_returns_positive() -> None:
    future = datetime.now(tz=UTC) + timedelta(seconds=300)
    iso = future.isoformat()
    result = seconds_until_iso(iso)
    # Allow a small tolerance for clock drift during test execution.
    assert 295 <= result <= 305


def test_past_timestamp_returns_zero() -> None:
    past = datetime.now(tz=UTC) - timedelta(hours=1)
    iso = past.isoformat()
    assert seconds_until_iso(iso) == 0


def test_invalid_format_returns_zero() -> None:
    assert seconds_until_iso("not-a-date") == 0


def test_empty_string_returns_zero() -> None:
    assert seconds_until_iso("") == 0


def test_z_suffix_handled() -> None:
    future = datetime.now(tz=UTC) + timedelta(seconds=120)
    iso = future.strftime("%Y-%m-%dT%H:%M:%SZ")
    result = seconds_until_iso(iso)
    assert 115 <= result <= 125


def test_naive_timestamp_assumed_utc() -> None:
    """A naive ISO string (no timezone) should be treated as UTC."""
    future = datetime.now(tz=UTC) + timedelta(seconds=60)
    iso = future.strftime("%Y-%m-%dT%H:%M:%S")
    result = seconds_until_iso(iso)
    assert 55 <= result <= 65


# ── codex_ts_to_iso ─────────────────────────────────────────────────


def test_codex_ts_to_iso_format() -> None:
    # 2024-01-15T12:00:00Z  ==  1705320000
    assert codex_ts_to_iso(1705320000) == "2024-01-15T12:00:00Z"


def test_codex_ts_to_iso_epoch_zero() -> None:
    assert codex_ts_to_iso(0) == "1970-01-01T00:00:00Z"


def test_round_trip() -> None:
    """codex_ts_to_iso output should be parseable by seconds_until_iso."""
    future = datetime.now(tz=UTC) + timedelta(seconds=200)
    unix_ts = int(future.timestamp())
    iso = codex_ts_to_iso(unix_ts)
    result = seconds_until_iso(iso)
    assert 195 <= result <= 205
