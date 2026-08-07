"""Tests for mr_overkill.refactor_suggest — refactoring entry point."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock, patch

from mr_overkill.models import (
    BudgetStatus,
    FinalStatus,
    LoopConfig,
    LoopResult,
)
from mr_overkill.refactor_suggest import (
    _get_budget_status,
    create_draft_pr,
    create_refactor_branch,
    resolve_auto_scope,
    run,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _budget(pct5: int, pct7: int | None = 0) -> BudgetStatus:
    return BudgetStatus(
        five_hour_used_pct=pct5,
        seven_day_used_pct=pct7,
        tokens_used=0,
        mode="oauth",
        tier="max5",
        resets_at=None,
    )


# ── resolve_auto_scope ──────────────────────────────────────────────


class TestResolveAutoScope:
    @patch("mr_overkill.refactor_suggest._get_budget_status")
    def test_module_scope_low_usage(self, mock_bs: MagicMock) -> None:
        mock_bs.return_value = _budget(20, 10)
        assert resolve_auto_scope(["claude", "codex"]) == "module"

    @patch("mr_overkill.refactor_suggest._get_budget_status")
    def test_micro_scope_medium_usage(self, mock_bs: MagicMock) -> None:
        mock_bs.return_value = _budget(75, 60)
        result = resolve_auto_scope(["claude"])
        assert result in {"micro", "module"}

    @patch("mr_overkill.refactor_suggest._get_budget_status")
    def test_none_when_7day_exhausted(self, mock_bs: MagicMock) -> None:
        mock_bs.return_value = _budget(10, 100)
        assert resolve_auto_scope(["claude"]) is None

    @patch("mr_overkill.refactor_suggest._get_budget_status")
    def test_none_when_all_too_high(self, mock_bs: MagicMock) -> None:
        mock_bs.return_value = _budget(95, 95)
        assert resolve_auto_scope(["claude"]) is None

    @patch("mr_overkill.refactor_suggest._get_budget_status")
    def test_skip_gate_resolves_module_when_exhausted(
        self, mock_bs: MagicMock
    ) -> None:
        mock_bs.return_value = _budget(100, 100)
        assert resolve_auto_scope(["claude"], skip_gate=True) == "module"
        mock_bs.assert_not_called()


# ── _get_budget_status ──────────────────────────────────────────────


class TestGetBudgetStatus:
    @patch("mr_overkill.refactor_suggest.claude_budget")
    def test_claude(self, mock_cb: MagicMock) -> None:
        expected = _budget(30)
        mock_cb.return_value = expected
        assert _get_budget_status("claude") == expected

    @patch("mr_overkill.refactor_suggest.codex_budget")
    def test_codex(self, mock_cb: MagicMock) -> None:
        expected = _budget(40)
        mock_cb.return_value = expected
        assert _get_budget_status("codex") == expected

    def test_unknown_tool(self) -> None:
        status = _get_budget_status("unknown-tool")
        assert status.five_hour_used_pct == 0


# ── create_refactor_branch ──────────────────────────────────────────


class TestCreateRefactorBranch:
    @patch("mr_overkill.refactor_suggest.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        branch = create_refactor_branch("module", "develop")
        assert branch is not None
        assert branch.startswith("refactor/module-")

    @patch("mr_overkill.refactor_suggest.subprocess.run")
    def test_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=1, stderr="error"
        )
        assert create_refactor_branch("module", "develop") is None


# ── create_draft_pr ─────────────────────────────────────────────────


class TestCreateDraftPr:
    @patch("mr_overkill.refactor_suggest.subprocess.run")
    def test_no_commits_skips(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="0\n"
        )
        assert create_draft_pr(
            "module", "develop", "refactor/module-x", 3, "all_clear"
        ) is True

    @patch("mr_overkill.refactor_suggest.subprocess.run")
    def test_creates_pr(self, mock_run: MagicMock) -> None:
        # rev-list returns ahead count, upstream check fails, push ok, gh ok
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="2\n"),      # rev-list
            MagicMock(returncode=1),                     # upstream check
            MagicMock(returncode=0, stdout="origin\n"),  # git remote
            MagicMock(returncode=0),                     # git push
            MagicMock(returncode=0, stdout="url\n"),     # gh pr create
        ]
        assert create_draft_pr(
            "module", "develop", "refactor/module-x", 3, "all_clear"
        ) is True


# ── run ─────────────────────────────────────────────────────────────


class TestRun:
    @patch("mr_overkill.refactor_suggest.git_all_dirty", return_value=[])
    @patch("mr_overkill.refactor_suggest.review_fix_loop")
    @patch("mr_overkill.refactor_suggest.resolve_auto_scope")
    @patch("mr_overkill.refactor_suggest.subprocess.run")
    def test_auto_scope_receives_skip_budget_gate(
        self,
        mock_run: MagicMock,
        mock_scope: MagicMock,
        mock_loop: MagicMock,
        _mock_dirty: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="src/a.py\n"
        )
        mock_scope.return_value = "module"
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=1,
        )
        config = make_loop_config(
            current_branch="refactor/module-20260301",
            skip_budget_gate=True,
        )
        assert run(config, "auto") == 0
        assert mock_scope.call_args.kwargs["skip_gate"] is True

    @patch("mr_overkill.refactor_suggest.git_all_dirty", return_value=[])
    @patch("mr_overkill.refactor_suggest.review_fix_loop")
    @patch("mr_overkill.refactor_suggest.git_all_dirty", return_value=[])
    @patch("mr_overkill.refactor_suggest.subprocess.run")
    def test_success_returns_zero(
        self,
        mock_run: MagicMock,
        mock_dirty: MagicMock,
        mock_loop: MagicMock,
        _mock_dirty: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="src/a.py\nsrc/b.py\n"
        )
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=1,
        )
        config = make_loop_config(current_branch="refactor/module-20260301")
        assert run(config, "module") == 0

    @patch("mr_overkill.refactor_suggest.git_all_dirty", return_value=[])
    @patch("mr_overkill.refactor_suggest.review_fix_loop")
    @patch("mr_overkill.refactor_suggest.git_all_dirty", return_value=[])
    @patch("mr_overkill.refactor_suggest.subprocess.run")
    def test_error_returns_one(
        self,
        mock_run: MagicMock,
        mock_dirty: MagicMock,
        mock_loop: MagicMock,
        _mock_dirty: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="src/a.py\n"
        )
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.CODEX_ERROR,
            iterations_run=0,
        )
        config = make_loop_config(current_branch="refactor/module-20260301")
        assert run(config, "module") == 1

    @patch("mr_overkill.refactor_suggest.git_all_dirty", return_value=[])
    @patch("mr_overkill.refactor_suggest.create_draft_pr")
    @patch("mr_overkill.refactor_suggest.review_fix_loop")
    @patch("mr_overkill.refactor_suggest.git_all_dirty", return_value=[])
    @patch("mr_overkill.refactor_suggest.subprocess.run")
    def test_create_pr_called(
        self,
        mock_run: MagicMock,
        mock_dirty: MagicMock,
        mock_loop: MagicMock,
        mock_pr: MagicMock,
        _mock_dirty: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="src/a.py\n"
        )
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=1,
        )
        config = make_loop_config(current_branch="refactor/module-20260301")
        run(config, "module", create_pr=True)
        mock_pr.assert_called_once()

    @patch("mr_overkill.refactor_suggest.git_all_dirty", return_value=[])
    @patch("mr_overkill.refactor_suggest.create_draft_pr")
    @patch("mr_overkill.refactor_suggest.review_fix_loop")
    @patch("mr_overkill.refactor_suggest.subprocess.run")
    def test_no_pr_on_dry_run(
        self,
        mock_run: MagicMock,
        mock_loop: MagicMock,
        mock_pr: MagicMock,
        _mock_dirty: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="src/a.py\n"
        )
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=1,
        )
        config = make_loop_config(
            current_branch="refactor/module-20260301", dry_run=True,
        )
        run(config, "module", create_pr=True)
        mock_pr.assert_not_called()
