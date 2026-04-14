"""Human-readable budget report for ``overkill check-budget``."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from mr_overkill.budget import budget_sufficient, has_threshold
from mr_overkill.budget.claude import check_token_budget as claude_check
from mr_overkill.budget.codex import check_token_budget as codex_check
from mr_overkill.models import BudgetScope, BudgetStatus

_HEADER = "\u2550" * 33  # ═════════════════════════════════


def _fmt_pct(pct: int | None) -> str:
    return f"{pct}%" if pct is not None else "—"


def _fmt_reset(resets_at: str | None) -> str:
    return f"    (resets {resets_at})" if resets_at else ""


def _print_claude(status: BudgetStatus) -> None:
    print("Claude Code Token Budget")
    print(_HEADER)
    print(f"  Mode:     {status.mode}")
    if status.tier:
        print(f"  Tier:     {status.tier}")
    print(
        f"  5h used:  {_fmt_pct(status.five_hour_used_pct)}"
        f"{_fmt_reset(status.resets_at)}"
    )
    print(
        f"  7d used:  {_fmt_pct(status.seven_day_used_pct)}"
        f"{_fmt_reset(status.seven_day_resets_at)}"
    )
    if status.tokens_used:
        print(f"  Tokens:   {status.tokens_used:,}")
    if status.estimated:
        print("  (estimated from local logs)")
    print()


def _print_codex(status: BudgetStatus) -> None:
    print("Codex Token Budget")
    print(_HEADER)
    print(f"  Mode:     {status.mode}")
    print(
        f"  5h used:  {_fmt_pct(status.five_hour_used_pct)}"
        f"{_fmt_reset(status.resets_at)}"
    )
    print(
        f"  7d used:  {_fmt_pct(status.seven_day_used_pct)}"
        f"{_fmt_reset(status.seven_day_resets_at)}"
    )
    print()


def _print_gemini() -> None:
    print("Gemini Budget")
    print(_HEADER)
    print("  No local budget data (API-key billing).")
    print()


def _print_scope_table(claude_st: BudgetStatus, codex_st: BudgetStatus) -> None:
    print("Scope Thresholds")
    print(_HEADER)
    print("           Claude  Codex   Gemini")
    scopes = (
        BudgetScope.MICRO, BudgetScope.MODULE,
        BudgetScope.LAYER, BudgetScope.FULL,
    )
    for scope in scopes:
        if has_threshold(scope):
            c_label = "GO" if budget_sufficient(scope, claude_st) else "NOGO"
            x_label = "GO" if budget_sufficient(scope, codex_st) else "NOGO"
        else:
            c_label = x_label = "—"
        print(f"  {scope.value:<8} {c_label:<7} {x_label:<7} —")


def print_budget_report(*, json_mode: bool = False) -> int:
    """Print budget report. Returns 0 on success, 1 on error."""
    try:
        claude_st = claude_check()
        codex_st = codex_check()
    except Exception as exc:
        print(f"Error fetching budget: {exc}", file=sys.stderr)
        return 1

    if json_mode:
        payload = {
            "claude": asdict(claude_st),
            "codex": asdict(codex_st),
            "gemini": {"mode": "api-key", "note": "no local budget data"},
        }
        print(json.dumps(payload, indent=2))
        return 0

    _print_claude(claude_st)
    _print_codex(codex_st)
    _print_gemini()
    _print_scope_table(claude_st, codex_st)
    return 0
