"""Tests for mr_overkill.budget.gemini — budget stub."""

from __future__ import annotations

from mr_overkill.budget.gemini import gemini_budget_sufficient
from mr_overkill.models import BudgetScope


class TestGeminiBudgetSufficient:
    def test_always_returns_true_micro(self) -> None:
        assert gemini_budget_sufficient(BudgetScope.MICRO) is True

    def test_always_returns_true_module(self) -> None:
        assert gemini_budget_sufficient(BudgetScope.MODULE) is True

    def test_always_returns_true_layer(self) -> None:
        assert gemini_budget_sufficient(BudgetScope.LAYER) is True

    def test_always_returns_true_full(self) -> None:
        assert gemini_budget_sufficient(BudgetScope.FULL) is True
