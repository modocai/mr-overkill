"""Integration tests — verify end-to-end wiring across modules.

These tests use mocked external tools (claude, codex, gh) but exercise
real module interactions: CLI parsing → config → loop_engine → review →
fix → commit pipeline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill.agents import create_fix_agent, create_review_agent
from mr_overkill.models import (
    FinalStatus,
    LoopConfig,
    LoopResult,
)
from mr_overkill.review_loop import run


class TestReviewLoopIntegration:
    """Test review_loop.run with real agent factories."""

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_run_wires_reviewer_and_fixer(
        self,
        mock_loop: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        """Verify that run() creates and passes real callables."""
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=1,
        )
        config = make_loop_config()
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


class TestCreateReviewAgentIntegration:
    """Test create_review_agent produces a working ReviewerFn."""

    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents.retry_codex_cmd")
    def test_reviewer_reads_prompt_and_calls_codex(
        self,
        mock_retry: MagicMock,
        mock_budget_factory: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_budget_factory.return_value = MagicMock(return_value=True)
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / "codex-review.prompt.md").write_text(
            "Review $CURRENT_BRANCH vs $TARGET_BRANCH iter $ITERATION"
        )

        config = make_loop_config(prompts_dir=prompts)
        reviewer = create_review_agent(config)

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
        self,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        # No prompt file created

        config = make_loop_config(prompts_dir=prompts)
        reviewer = create_review_agent(config)

        output = tmp_path / "review.json"
        result = reviewer(output, 1)

        assert result is False


class TestCreateFixAgentIntegration:
    """Test create_fix_agent produces a working FixFn."""

    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents._make_retry_fn")
    @patch("mr_overkill.agents.claude_two_step_fix")
    def test_fixer_calls_two_step_fix(
        self,
        mock_tsf: MagicMock,
        mock_retry_factory: MagicMock,
        mock_budget_factory: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_retry_factory.return_value = MagicMock()
        mock_budget_factory.return_value = MagicMock()
        config = make_loop_config()
        fixer = create_fix_agent(config)

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
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from mr_overkill.__main__ import main

        monkeypatch.setattr("sys.argv", ["overkill", "review-loop"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

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
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from mr_overkill.__main__ import main

        monkeypatch.setattr("sys.argv", ["overkill", "refactor-suggest"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_unknown_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mr_overkill.__main__ import main

        monkeypatch.setattr("sys.argv", ["overkill", "nonsense"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_no_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mr_overkill.__main__ import main

        monkeypatch.setattr("sys.argv", ["overkill"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
