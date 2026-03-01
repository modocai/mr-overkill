"""Tests for budget go/no-go policy and codex_parse_window."""

from __future__ import annotations

from mr_overkill.budget import budget_sufficient, codex_parse_window
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
