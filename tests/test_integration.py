"""Integration tests — verify end-to-end wiring across modules.

These tests use mocked external tools (claude, codex, gh) but exercise
real module interactions: CLI parsing → config → loop_engine → review →
fix → commit pipeline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill.models import (
    FinalStatus,
    LoopConfig,
    LoopResult,
)
from mr_overkill.review_loop import _make_fixer, _make_reviewer, run


def _make_config(tmp_path: Path, **overrides: object) -> LoopConfig:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    defaults = {
        "current_branch": "feat/test",
        "target_branch": "develop",
        "max_loop": 1,
        "max_subloop": 0,
        "log_dir": log_dir,
        "prompts_dir": prompts_dir,
    }
    defaults.update(overrides)
    return LoopConfig(**defaults)  # type: ignore[arg-type]


class TestReviewLoopIntegration:
    """Test review_loop.run with real _make_reviewer / _make_fixer."""

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_run_wires_reviewer_and_fixer(
        self, mock_loop: MagicMock, tmp_path: Path
    ) -> None:
        """Verify that run() creates and passes real callables."""
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=1,
        )
        config = _make_config(tmp_path)
        result = run(config)

        assert result == 0
        mock_loop.assert_called_once()
        call_kwargs = mock_loop.call_args
        # Verify reviewer and fixer are callable
        assert callable(
            call_kwargs.kwargs.get("reviewer")
            or call_kwargs[1].get("reviewer")
        )
        assert callable(
            call_kwargs.kwargs.get("fixer")
            or call_kwargs[1].get("fixer")
        )


class TestMakeReviewerIntegration:
    """Test _make_reviewer produces a working ReviewerFn."""

    @patch("mr_overkill.review_loop._wait_budget", return_value=True)
    @patch("mr_overkill.review_loop.retry_codex_cmd")
    def test_reviewer_reads_prompt_and_calls_codex(
        self,
        mock_retry: MagicMock,
        mock_budget: MagicMock,
        tmp_path: Path,
    ) -> None:
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / "codex-review.prompt.md").write_text(
            "Review $CURRENT_BRANCH vs $TARGET_BRANCH iter $ITERATION"
        )

        config = _make_config(tmp_path, prompts_dir=prompts)
        reviewer = _make_reviewer(config)

        output = tmp_path / "review.json"
        mock_retry.return_value = True

        result = reviewer(output, 1)

        assert result is True
        mock_retry.assert_called_once()
        # Verify the prompt was rendered
        call_args = mock_retry.call_args[0]
        cmd_args = call_args[2]
        prompt_text = cmd_args[-1]
        assert "feat/test" in prompt_text
        assert "develop" in prompt_text

    def test_reviewer_fails_on_missing_prompt(
        self, tmp_path: Path
    ) -> None:
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        # No prompt file created

        config = _make_config(tmp_path, prompts_dir=prompts)
        reviewer = _make_reviewer(config)

        output = tmp_path / "review.json"
        result = reviewer(output, 1)

        assert result is False


class TestMakeFixerIntegration:
    """Test _make_fixer produces a working FixFn."""

    @patch("mr_overkill.review_loop._wait_budget", return_value=True)
    @patch("mr_overkill.review_loop.claude_two_step_fix")
    def test_fixer_calls_two_step_fix(
        self,
        mock_tsf: MagicMock,
        mock_budget: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = _make_config(tmp_path)
        fixer = _make_fixer(config)

        review_json = json.dumps({"findings": []})
        mock_tsf.return_value = True

        result = fixer(review_json, "test-fix")

        assert result is True
        mock_tsf.assert_called_once()


class TestCliEntryPoint:
    """Test __main__.py dispatch."""

    @patch("mr_overkill.review_loop.run", return_value=0)
    @patch(
        "mr_overkill.cli.parse_review_loop_args",
        return_value=MagicMock(),
    )
    def test_review_loop_dispatch(
        self,
        mock_parse: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        from mr_overkill.__main__ import main

        original_argv = sys.argv
        try:
            sys.argv = ["mr-overkill", "review-loop"]
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = original_argv

    @patch(
        "mr_overkill.refactor_suggest.run",
        return_value=0,
    )
    @patch(
        "mr_overkill.cli.parse_refactor_suggest_args",
        return_value=(
            MagicMock(scope="module"),
            MagicMock(create_pr=False),
        ),
    )
    def test_refactor_suggest_dispatch(
        self,
        mock_parse: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        from mr_overkill.__main__ import main

        original_argv = sys.argv
        try:
            sys.argv = ["mr-overkill", "refactor-suggest"]
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = original_argv

    def test_unknown_command(self) -> None:
        from mr_overkill.__main__ import main

        original_argv = sys.argv
        try:
            sys.argv = ["mr-overkill", "nonsense"]
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        finally:
            sys.argv = original_argv

    def test_no_command(self) -> None:
        from mr_overkill.__main__ import main

        original_argv = sys.argv
        try:
            sys.argv = ["mr-overkill"]
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        finally:
            sys.argv = original_argv
