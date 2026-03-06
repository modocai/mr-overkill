"""Tests for budget_report module."""

from __future__ import annotations

import json
from unittest.mock import patch

from mr_overkill.budget_report import print_budget_report
from mr_overkill.models import BudgetStatus

_CLAUDE = BudgetStatus(
    five_hour_used_pct=42,
    seven_day_used_pct=18,
    tokens_used=2_100_000,
    mode="oauth",
    tier="max5",
    resets_at="2026-03-03T15:30:00Z",
    seven_day_resets_at="2026-03-07T00:00:00Z",
)

_CODEX = BudgetStatus(
    five_hour_used_pct=15,
    seven_day_used_pct=8,
    tokens_used=0,
    mode="session_log",
    tier="",
    resets_at="2026-03-03T16:00:00Z",
)


def _patch_checks():
    return (
        patch("mr_overkill.budget_report.claude_check", return_value=_CLAUDE),
        patch("mr_overkill.budget_report.codex_check", return_value=_CODEX),
    )


class TestTextReport:
    def test_contains_claude_fields(self, capsys):
        p1, p2 = _patch_checks()
        with p1, p2:
            rc = print_budget_report()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Claude Code Token Budget" in out
        assert "oauth" in out
        assert "max5" in out
        assert "42%" in out
        assert "18%" in out
        assert "2,100,000" in out

    def test_contains_codex_fields(self, capsys):
        p1, p2 = _patch_checks()
        with p1, p2:
            rc = print_budget_report()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Codex Token Budget" in out
        assert "session_log" in out
        assert "15%" in out
        assert "8%" in out

    def test_scope_table_present(self, capsys):
        p1, p2 = _patch_checks()
        with p1, p2:
            print_budget_report()
        out = capsys.readouterr().out
        assert "Scope Thresholds" in out
        assert "micro" in out
        assert "GO" in out

    def test_resets_at_shown(self, capsys):
        p1, p2 = _patch_checks()
        with p1, p2:
            print_budget_report()
        out = capsys.readouterr().out
        assert "2026-03-03T15:30:00Z" in out
        assert "2026-03-03T16:00:00Z" in out


class TestJsonReport:
    def test_json_output_parseable(self, capsys):
        p1, p2 = _patch_checks()
        with p1, p2:
            rc = print_budget_report(json_mode=True)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert "claude" in data
        assert "codex" in data

    def test_json_claude_fields(self, capsys):
        p1, p2 = _patch_checks()
        with p1, p2:
            print_budget_report(json_mode=True)
        data = json.loads(capsys.readouterr().out)
        assert data["claude"]["mode"] == "oauth"
        assert data["claude"]["five_hour_used_pct"] == 42
        assert data["claude"]["tier"] == "max5"

    def test_json_codex_fields(self, capsys):
        p1, p2 = _patch_checks()
        with p1, p2:
            print_budget_report(json_mode=True)
        data = json.loads(capsys.readouterr().out)
        assert data["codex"]["mode"] == "session_log"
        assert data["codex"]["five_hour_used_pct"] == 15


class TestErrorHandling:
    def test_exception_returns_1(self, capsys):
        p = patch(
            "mr_overkill.budget_report.claude_check",
            side_effect=RuntimeError("fail"),
        )
        with p:
            rc = print_budget_report()
        assert rc == 1
        assert "fail" in capsys.readouterr().err
