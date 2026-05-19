"""Tests for mr_overkill.review_loop — entry point wiring."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from mr_overkill.models import FinalStatus, LoopConfig, LoopResult
from mr_overkill.review_loop import run


class TestReviewLoopRun:
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_success_returns_zero(
        self,
        mock_loop: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=1,
            summary_path=tmp_path / "summary.md",
        )
        config = make_loop_config()
        assert run(config) == 0

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_dry_run_returns_zero(
        self,
        mock_loop: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.DRY_RUN,
            iterations_run=1,
        )
        config = make_loop_config(dry_run=True)
        assert run(config) == 0

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_error_returns_one(
        self,
        mock_loop: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.CLAUDE_ERROR,
            iterations_run=0,
        )
        config = make_loop_config()
        assert run(config) == 1

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_max_iterations_returns_zero(
        self,
        mock_loop: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.MAX_ITERATIONS_REACHED,
            iterations_run=3,
        )
        config = make_loop_config()
        assert run(config) == 0


class TestCITriggerCommit:
    """`--ci-trigger-mode last-only` emits one empty CI trigger commit on PASS."""

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_trigger_commit_emitted_on_all_clear_last_only(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
        )
        config = make_loop_config(ci_trigger_mode="last-only")
        run(config)
        mock_trigger.assert_called_once_with(branch=config.current_branch)

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_in_every_mode(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
        )
        config = make_loop_config(ci_trigger_mode="every")
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_in_none_mode(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
        )
        config = make_loop_config(ci_trigger_mode="none")
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_on_max_iterations(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.MAX_ITERATIONS_REACHED,
            iterations_run=5,
        )
        config = make_loop_config(ci_trigger_mode="last-only")
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_on_error(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.CLAUDE_ERROR,
            iterations_run=1,
        )
        config = make_loop_config(ci_trigger_mode="last-only")
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_with_no_auto_commit(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
        )
        config = make_loop_config(
            ci_trigger_mode="last-only", auto_commit=False,
        )
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_in_dry_run(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
        )
        config = make_loop_config(
            ci_trigger_mode="last-only", dry_run=True,
        )
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_trigger_commit_push_failure_does_not_raise(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        """A failed CI trigger push should not crash the run() exit path."""
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
        )
        mock_trigger.side_effect = RuntimeError("git push failed: no remote")
        config = make_loop_config(ci_trigger_mode="last-only")
        # Should still return 0 since the loop itself succeeded.
        assert run(config) == 0
