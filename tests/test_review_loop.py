"""Tests for mr_overkill.review_loop — entry point wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from mr_overkill.models import FinalStatus, LoopConfig, LoopResult
from mr_overkill.review_loop import run


def _make_config(tmp_path: Path, **overrides: object) -> LoopConfig:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    defaults = {
        "current_branch": "feat/test",
        "target_branch": "develop",
        "max_loop": 1,
        "log_dir": log_dir,
        "prompts_dir": tmp_path / "prompts",
    }
    defaults.update(overrides)
    return LoopConfig(**defaults)  # type: ignore[arg-type]


class TestReviewLoopRun:
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_success_returns_zero(
        self, mock_loop: MagicMock, tmp_path: Path
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=1,
            summary_path=tmp_path / "summary.md",
        )
        config = _make_config(tmp_path)
        assert run(config) == 0

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_dry_run_returns_zero(
        self, mock_loop: MagicMock, tmp_path: Path
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.DRY_RUN,
            iterations_run=1,
        )
        config = _make_config(tmp_path, dry_run=True)
        assert run(config) == 0

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_error_returns_one(
        self, mock_loop: MagicMock, tmp_path: Path
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.CLAUDE_ERROR,
            iterations_run=0,
        )
        config = _make_config(tmp_path)
        assert run(config) == 1

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_max_iterations_returns_zero(
        self, mock_loop: MagicMock, tmp_path: Path
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.MAX_ITERATIONS_REACHED,
            iterations_run=3,
        )
        config = _make_config(tmp_path)
        assert run(config) == 0
