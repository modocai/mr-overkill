"""Tests for budget go/no-go policy, codex_parse_window, and Claude budget."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mr_overkill.budget import budget_sufficient, codex_parse_window
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
        assert _sum_usage(usage) == 100  # 1000 * 0.1

    def test_all_fields(self) -> None:
        usage = {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_creation_input_tokens": 400,
            "cache_read_input_tokens": 10000,
        }
        # 100 + 200 + 400*0.25 + 10000*0.1 = 100+200+100+1000 = 1400
        assert _sum_usage(usage) == 1400

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

    @patch("mr_overkill.budget.claude.check_oauth")
    def test_oauth_success_uses_oauth(
        self, mock_oauth: object, tmp_path: Path,
    ) -> None:
        from unittest.mock import MagicMock
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

    @patch("mr_overkill.budget.claude.check_oauth", return_value=None)
    @patch("mr_overkill.budget.claude.check_local")
    def test_local_higher_than_cache_uses_local(
        self, mock_local: object, mock_oauth: object, tmp_path: Path,
    ) -> None:
        from unittest.mock import MagicMock
        mock_local_fn = MagicMock(return_value=self._local_status(pct=60))
        cache_file = tmp_path / "budget.json"
        # Cache has 5h=30%, 7d=10%
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
            patch("mr_overkill.budget.claude.check_local", mock_local_fn),
            patch("mr_overkill.budget.claude._CACHE_PATH", cache_file),
        ):
            result = check_token_budget()
        # Local 60% > cached 30%, uses local but carries 7-day from cache
        assert result.mode == "local"
        assert result.five_hour_used_pct == 60
        assert result.seven_day_used_pct == 10

    @patch("mr_overkill.budget.claude.check_oauth", return_value=None)
    @patch("mr_overkill.budget.claude.check_local")
    def test_no_cache_uses_local(
        self, mock_local: object, mock_oauth: object, tmp_path: Path,
    ) -> None:
        from unittest.mock import MagicMock
        mock_local_fn = MagicMock(return_value=self._local_status(pct=47))
        with (
            patch("mr_overkill.budget.claude.check_local", mock_local_fn),
            patch(
                "mr_overkill.budget.claude._CACHE_PATH",
                tmp_path / "nope.json",
            ),
        ):
            result = check_token_budget()
        assert result.mode == "local"
        assert result.five_hour_used_pct == 47
