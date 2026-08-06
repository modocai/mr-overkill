"""Tests for budget go/no-go policy, codex_parse_window, and Claude budget."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill.budget import (
    FIVE_HOUR_WINDOW,
    SEVEN_DAY_WINDOW,
    budget_gate_disabled,
    budget_sufficient,
    codex_parse_window,
    codex_window_kind,
)
from mr_overkill.budget.claude import (
    _load_cache,
    _save_cache,
    _sum_usage,
    check_token_budget,
)
from mr_overkill.models import BudgetScope, BudgetStatus


def _status(
    pct: int | None = 0,
    pct_7d: int | None = None,
    **kw: object,
) -> BudgetStatus:
    return BudgetStatus(
        five_hour_used_pct=pct,
        seven_day_used_pct=pct_7d,
        tokens_used=0,
        mode="test",
        tier="pro",
        resets_at=None,
        **kw,  # type: ignore[arg-type]
    )


# ── budget_sufficient: 5-hour thresholds ────────────────────────────


class TestFiveHourThresholds:
    def test_micro_below_threshold(self) -> None:
        assert budget_sufficient(BudgetScope.MICRO, _status(pct=89)) is True

    def test_micro_at_threshold(self) -> None:
        assert budget_sufficient(BudgetScope.MICRO, _status(pct=90)) is False

    def test_module_below_threshold(self) -> None:
        assert budget_sufficient(BudgetScope.MODULE, _status(pct=74)) is True

    def test_module_at_threshold(self) -> None:
        assert budget_sufficient(BudgetScope.MODULE, _status(pct=75)) is False

    def test_layer_always_go(self) -> None:
        assert budget_sufficient(BudgetScope.LAYER, _status(pct=99)) is True

    def test_full_always_go(self) -> None:
        assert budget_sufficient(BudgetScope.FULL, _status(pct=99)) is True

    def test_none_pct_assumed_ok(self) -> None:
        assert budget_sufficient(BudgetScope.MODULE, _status(pct=None)) is True


# ── budget_sufficient: 7-day guard ──────────────────────────────────


class TestSevenDayGuard:
    def test_7d_exhausted_always_nogo(self) -> None:
        assert (
            budget_sufficient(BudgetScope.MICRO, _status(pct=0, pct_7d=100)) is False
        )

    def test_7d_90_nogo_for_module(self) -> None:
        assert (
            budget_sufficient(BudgetScope.MODULE, _status(pct=0, pct_7d=90)) is False
        )

    def test_7d_90_ok_for_micro(self) -> None:
        assert (
            budget_sufficient(BudgetScope.MICRO, _status(pct=0, pct_7d=90)) is True
        )

    def test_7d_89_ok_for_module(self) -> None:
        assert (
            budget_sufficient(BudgetScope.MODULE, _status(pct=0, pct_7d=89)) is True
        )


# ── codex_parse_window ──────────────────────────────────────────────


class TestCodexParseWindow:
    def test_none_window_returns_default(self) -> None:
        assert codex_parse_window(None, 1000, default_pct=42) == (42, None)

    def test_empty_window_returns_default(self) -> None:
        assert codex_parse_window({}, 1000, default_pct=0) == (0, None)

    def test_expired_window_returns_zero(self) -> None:
        window = {"resets_at": 900, "used_percent": 80.0}
        assert codex_parse_window(window, 1000) == (0, None)

    def test_active_window_with_resets(self) -> None:
        window = {"resets_at": 2000, "used_percent": 45.7}
        pct, resets = codex_parse_window(window, 1000)
        assert pct == 46  # rounded
        assert resets is not None
        assert "1970-01-01" in resets  # epoch 2000

    def test_window_no_used_percent_returns_default(self) -> None:
        window = {"resets_at": 2000}
        assert codex_parse_window(window, 1000, default_pct=5) == (5, None)

    def test_window_no_resets_at(self) -> None:
        window = {"used_percent": 30.0}
        pct, resets = codex_parse_window(window, 1000)
        assert pct == 30
        assert resets is None


# ── codex_window_kind ───────────────────────────────────────────────


class TestCodexWindowKind:
    def test_five_hour_window(self) -> None:
        assert codex_window_kind({"window_minutes": 300}) == FIVE_HOUR_WINDOW

    def test_weekly_window(self) -> None:
        assert codex_window_kind({"window_minutes": 10080}) == SEVEN_DAY_WINDOW

    def test_daily_boundary_counts_as_short(self) -> None:
        assert codex_window_kind({"window_minutes": 1440}) == FIVE_HOUR_WINDOW

    def test_just_over_daily_counts_as_weekly(self) -> None:
        assert codex_window_kind({"window_minutes": 1441}) == SEVEN_DAY_WINDOW

    def test_string_minutes(self) -> None:
        assert codex_window_kind({"window_minutes": "10080"}) == SEVEN_DAY_WINDOW

    def test_missing_minutes_is_unclassified(self) -> None:
        assert codex_window_kind({"used_percent": 50.0}) is None

    def test_malformed_minutes_is_unclassified(self) -> None:
        assert codex_window_kind({"window_minutes": "soon"}) is None

    def test_non_positive_minutes_is_unclassified(self) -> None:
        assert codex_window_kind({"window_minutes": 0}) is None

    def test_empty_window(self) -> None:
        assert codex_window_kind(None) is None
        assert codex_window_kind({}) is None


# ── budget_gate_disabled ────────────────────────────────────────────


class TestBudgetGateDisabled:
    def test_unset_is_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OVERKILL_SKIP_BUDGET", raising=False)
        assert budget_gate_disabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
    def test_truthy_values(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("OVERKILL_SKIP_BUDGET", value)
        assert budget_gate_disabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no"])
    def test_falsy_values(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("OVERKILL_SKIP_BUDGET", value)
        assert budget_gate_disabled() is False


# ── _sum_usage: rate-limit weights ───────────────────────────────


class TestSumUsage:
    def test_basic_tokens(self) -> None:
        usage = {"input_tokens": 100, "output_tokens": 200}
        assert _sum_usage(usage) == 300

    def test_cache_creation_weighted(self) -> None:
        usage = {"input_tokens": 0, "cache_creation_input_tokens": 1000}
        assert _sum_usage(usage) == 250  # 1000 * 0.25

    def test_cache_read_weighted(self) -> None:
        usage = {"input_tokens": 0, "cache_read_input_tokens": 1000}
        assert _sum_usage(usage) == 80  # 1000 * 0.08

    def test_all_fields(self) -> None:
        usage = {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_creation_input_tokens": 400,
            "cache_read_input_tokens": 10000,
        }
        # 100 + 200 + 400*0.25 + 10000*0.08 = 100+200+100+800 = 1200
        assert _sum_usage(usage) == 1200

    def test_empty_usage(self) -> None:
        assert _sum_usage({}) == 0


# ── OAuth cache ──────────────────────────────────────────────────


class TestOAuthCache:
    def test_save_and_load(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "budget.json"
        status = BudgetStatus(
            five_hour_used_pct=45,
            seven_day_used_pct=30,
            tokens_used=0,
            mode="oauth",
            tier="max5",
            resets_at="2099-01-01T00:00:00+00:00",
            seven_day_resets_at="2099-01-01T00:00:00+00:00",
            estimated=False,
        )
        with patch("mr_overkill.budget.claude._CACHE_PATH", cache_file):
            _save_cache(status)
            loaded = _load_cache()

        assert loaded is not None
        assert loaded.five_hour_used_pct == 45
        assert loaded.seven_day_used_pct == 30
        assert loaded.mode == "cached"
        assert loaded.estimated is True

    def test_expired_cache_returns_none(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "budget.json"
        # Both windows already expired
        cache_file.write_text(json.dumps({
            "five_hour_used_pct": 45,
            "seven_day_used_pct": 30,
            "tokens_used": 0,
            "mode": "oauth",
            "tier": "max5",
            "resets_at": "2000-01-01T00:00:00+00:00",
            "seven_day_resets_at": "2000-01-01T00:00:00+00:00",
            "estimated": False,
        }))
        with patch("mr_overkill.budget.claude._CACHE_PATH", cache_file):
            assert _load_cache() is None

    def test_missing_cache_returns_none(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "nonexistent.json"
        with patch("mr_overkill.budget.claude._CACHE_PATH", cache_file):
            assert _load_cache() is None


# ── check_token_budget: merge logic ──────────────────────────────


class TestCheckTokenBudgetMerge:
    def _oauth_status(self, pct: int = 20) -> BudgetStatus:
        return BudgetStatus(
            five_hour_used_pct=pct,
            seven_day_used_pct=10,
            tokens_used=0,
            mode="oauth",
            tier="max5",
            resets_at="2099-01-01T00:00:00+00:00",
            seven_day_resets_at="2099-01-01T00:00:00+00:00",
            estimated=False,
        )

    def _local_status(self, pct: int = 50) -> BudgetStatus:
        return BudgetStatus(
            five_hour_used_pct=pct,
            seven_day_used_pct=None,
            tokens_used=1000,
            mode="local",
            tier="max5",
            resets_at=None,
            estimated=True,
        )

    def test_oauth_success_uses_oauth(self, tmp_path: Path) -> None:
        mock_oauth = MagicMock(return_value=self._oauth_status())
        with (
            patch("mr_overkill.budget.claude.check_oauth", mock_oauth),
            patch(
                "mr_overkill.budget.claude._CACHE_PATH",
                tmp_path / "budget.json",
            ),
        ):
            result = check_token_budget()
        assert result.mode == "oauth"
        assert result.five_hour_used_pct == 20

    def test_cached_oauth_preferred_over_local(
        self, tmp_path: Path,
    ) -> None:
        mock_local = MagicMock(return_value=self._local_status(pct=60))
        cache_file = tmp_path / "budget.json"
        # Cache has 5h=30%, 7d=10% — but local=60% is higher
        cache_file.write_text(json.dumps({
            "five_hour_used_pct": 30,
            "seven_day_used_pct": 10,
            "tokens_used": 0,
            "mode": "oauth",
            "tier": "max5",
            "resets_at": "2099-01-01T00:00:00+00:00",
            "seven_day_resets_at": "2099-01-01T00:00:00+00:00",
            "estimated": False,
        }))
        with (
            patch("mr_overkill.budget.claude.check_oauth", return_value=None),
            patch("mr_overkill.budget.claude.check_local", mock_local),
            patch("mr_overkill.budget.claude._CACHE_PATH", cache_file),
        ):
            result = check_token_budget()
        # Conservative: use max(cached, local) for 5h to avoid underreporting
        assert result.mode == "cached"
        assert result.five_hour_used_pct == 60
        assert result.seven_day_used_pct == 10

    def test_no_cache_uses_local(self, tmp_path: Path) -> None:
        mock_local = MagicMock(return_value=self._local_status(pct=47))
        with (
            patch("mr_overkill.budget.claude.check_oauth", return_value=None),
            patch("mr_overkill.budget.claude.check_local", mock_local),
            patch(
                "mr_overkill.budget.claude._CACHE_PATH",
                tmp_path / "nope.json",
            ),
        ):
            result = check_token_budget()
        assert result.mode == "local"
        assert result.five_hour_used_pct == 47
