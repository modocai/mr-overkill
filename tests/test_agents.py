"""Tests for mr_overkill.agents — ABC implementations and factory functions."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from mr_overkill.agents import (
    ClaudeFixAgent,
    ClaudeRefactorReviewAgent,
    ClaudeReviewAgent,
    ClaudeSelfReviewAgent,
    CodexRefactorReviewAgent,
    CodexReviewAgent,
    FixAgent,
    GeminiRefactorReviewAgent,
    GeminiReviewAgent,
    ReviewAgent,
    SelfReviewAgent,
    _BudgetFn,
    create_fix_agent,
    create_review_agent,
    create_self_review_agent,
)
from mr_overkill.models import BudgetScope, LoopConfig

# ── ReviewAgent implementations ─────────────────────────────────────


class TestCodexReviewAgent:
    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents.retry_codex_cmd", return_value=True)
    def test_calls_codex_with_rendered_prompt(
        self,
        mock_retry: MagicMock,
        mock_budget_factory: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_budget_factory.return_value = MagicMock(
            return_value=True
        )
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / "codex-review.prompt.md").write_text(
            "Review $CURRENT_BRANCH vs $TARGET_BRANCH iter $ITERATION"
        )

        config = make_loop_config(prompts_dir=prompts)
        agent = CodexReviewAgent(config)

        output = tmp_path / "review.json"
        assert agent(output, 1) is True
        mock_retry.assert_called_once()

        cmd_args = mock_retry.call_args[0][2]
        prompt_text = cmd_args[-1]
        assert "feat/test" in prompt_text
        assert "develop" in prompt_text

    def test_fails_on_missing_prompt(
        self,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)

        config = make_loop_config(prompts_dir=prompts)
        agent = CodexReviewAgent(config)

        output = tmp_path / "review.json"
        assert agent(output, 1) is False


class TestCodexRefactorReviewAgent:
    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents.retry_codex_cmd", return_value=True)
    @patch("mr_overkill.agents.subprocess.run")
    def test_calls_codex_with_scope_prompt(
        self,
        mock_subprocess: MagicMock,
        mock_retry: MagicMock,
        mock_budget_factory: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_budget_factory.return_value = MagicMock(
            return_value=True
        )
        mock_subprocess.return_value = MagicMock(
            returncode=0, stdout="src/a.py\n"
        )
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / "codex-refactor-module.prompt.md").write_text(
            "Refactor $CURRENT_BRANCH scope $SOURCE_FILES_PATH"
        )

        config = make_loop_config(prompts_dir=prompts)
        agent = CodexRefactorReviewAgent(config, "module")

        output = tmp_path / "review.json"
        assert agent(output, 1) is True
        mock_retry.assert_called_once()


# ── ClaudeReviewAgent ────────────────────────────────────────────────


class TestClaudeReviewAgent:
    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents._make_retry_fn")
    def test_calls_claude_with_rendered_prompt(
        self,
        mock_retry_factory: MagicMock,
        mock_budget_factory: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_budget_factory.return_value = MagicMock(
            return_value=True
        )
        mock_retry = MagicMock(return_value=True)
        mock_retry_factory.return_value = mock_retry

        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / "claude-review.prompt.md").write_text(
            "Review $CURRENT_BRANCH vs $TARGET_BRANCH iter $ITERATION"
        )

        config = make_loop_config(prompts_dir=prompts)
        agent = ClaudeReviewAgent(config)

        output = tmp_path / "review.json"
        assert agent(output, 1) is True
        mock_retry.assert_called_once()

        call_kw = mock_retry.call_args.kwargs
        assert "feat/test" in call_kw["stdin"]
        assert "develop" in call_kw["stdin"]

    def test_fails_on_missing_prompt(
        self,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)

        config = make_loop_config(prompts_dir=prompts)
        agent = ClaudeReviewAgent(config)

        output = tmp_path / "review.json"
        assert agent(output, 1) is False


class TestClaudeRefactorReviewAgent:
    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents._make_retry_fn")
    @patch("mr_overkill.agents.subprocess.run")
    def test_calls_claude_with_scope_prompt(
        self,
        mock_subprocess: MagicMock,
        mock_retry_factory: MagicMock,
        mock_budget_factory: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_budget_factory.return_value = MagicMock(
            return_value=True
        )
        mock_retry = MagicMock(return_value=True)
        mock_retry_factory.return_value = mock_retry
        mock_subprocess.return_value = MagicMock(
            returncode=0, stdout="src/a.py\n"
        )
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / "claude-refactor-module.prompt.md").write_text(
            "Refactor $CURRENT_BRANCH scope $SOURCE_FILES_PATH"
        )

        config = make_loop_config(prompts_dir=prompts)
        agent = ClaudeRefactorReviewAgent(config, "module")

        output = tmp_path / "review.json"
        assert agent(output, 1) is True
        mock_retry.assert_called_once()

        call_kw = mock_retry.call_args.kwargs
        assert "feat/test" in call_kw["stdin"]


# ── GeminiReviewAgent ────────────────────────────────────────────────


class TestGeminiReviewAgent:
    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents.retry_gemini_cmd", return_value=True)
    def test_calls_gemini_with_rendered_prompt(
        self,
        mock_retry: MagicMock,
        mock_budget_factory: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_budget_factory.return_value = MagicMock(
            return_value=True
        )
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / "gemini-review.prompt.md").write_text(
            "Review $CURRENT_BRANCH vs $TARGET_BRANCH iter $ITERATION"
        )

        config = make_loop_config(
            prompts_dir=prompts, reviewer_backend="gemini"
        )
        agent = GeminiReviewAgent(config)

        output = tmp_path / "review.json"
        assert agent(output, 1) is True
        mock_retry.assert_called_once()

        call_kw = mock_retry.call_args.kwargs
        assert "feat/test" in call_kw["stdin"]
        assert "develop" in call_kw["stdin"]

    def test_fails_on_missing_prompt(
        self,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)

        config = make_loop_config(
            prompts_dir=prompts, reviewer_backend="gemini"
        )
        agent = GeminiReviewAgent(config)

        output = tmp_path / "review.json"
        assert agent(output, 1) is False


class TestGeminiRefactorReviewAgent:
    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents.retry_gemini_cmd", return_value=True)
    @patch("mr_overkill.agents.subprocess.run")
    def test_calls_gemini_with_scope_prompt(
        self,
        mock_subprocess: MagicMock,
        mock_retry: MagicMock,
        mock_budget_factory: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_budget_factory.return_value = MagicMock(
            return_value=True
        )
        mock_subprocess.return_value = MagicMock(
            returncode=0, stdout="src/a.py\n"
        )
        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / "gemini-refactor-module.prompt.md").write_text(
            "Refactor $CURRENT_BRANCH scope $SOURCE_FILES_PATH"
        )

        config = make_loop_config(
            prompts_dir=prompts, reviewer_backend="gemini"
        )
        agent = GeminiRefactorReviewAgent(config, "module")

        output = tmp_path / "review.json"
        assert agent(output, 1) is True
        mock_retry.assert_called_once()

        call_kw = mock_retry.call_args.kwargs
        assert "feat/test" in call_kw["stdin"]


# ── ClaudeFixAgent ───────────────────────────────────────────────────


class TestClaudeFixAgent:
    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents._make_retry_fn")
    @patch("mr_overkill.agents.claude_two_step_fix", return_value=True)
    def test_default_prompts(
        self,
        mock_tsf: MagicMock,
        mock_retry_factory: MagicMock,
        mock_budget_factory: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_retry_factory.return_value = MagicMock()
        mock_budget_factory.return_value = MagicMock()
        config = make_loop_config()
        agent = ClaudeFixAgent(config)

        review_json = json.dumps({"findings": []})
        assert agent(review_json, "test-fix") is True
        mock_tsf.assert_called_once()
        kw = mock_tsf.call_args.kwargs
        assert kw["opinion_prompt"] == "claude-fix.prompt.md"
        assert kw["execute_prompt"] == "claude-fix-execute.prompt.md"

    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents._make_retry_fn")
    @patch("mr_overkill.agents.claude_two_step_fix", return_value=True)
    def test_refactor_prompts(
        self,
        mock_tsf: MagicMock,
        mock_retry_factory: MagicMock,
        mock_budget_factory: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_retry_factory.return_value = MagicMock()
        mock_budget_factory.return_value = MagicMock()
        config = make_loop_config()
        agent = ClaudeFixAgent(
            config,
            opinion_prompt="claude-refactor-fix.prompt.md",
            execute_prompt="claude-refactor-fix-execute.prompt.md",
        )

        review_json = json.dumps({"findings": []})
        assert agent(review_json, "test-fix") is True
        kw = mock_tsf.call_args.kwargs
        assert kw["opinion_prompt"] == "claude-refactor-fix.prompt.md"
        assert kw["execute_prompt"] == (
            "claude-refactor-fix-execute.prompt.md"
        )


# ── SelfReviewAgent ──────────────────────────────────────────────────


class TestClaudeSelfReviewAgent:
    @patch("mr_overkill.agents._make_budget_fn")
    @patch("mr_overkill.agents._make_retry_fn")
    @patch(
        "mr_overkill.agents.self_review_subloop",
        return_value="sub-1: ok",
    )
    def test_delegates_to_subloop(
        self,
        mock_subloop: MagicMock,
        mock_retry_factory: MagicMock,
        mock_budget_factory: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_retry_factory.return_value = MagicMock()
        mock_budget_factory.return_value = MagicMock()
        config = make_loop_config()

        fixer: FixAgent = MagicMock(spec=ClaudeFixAgent)
        agent = ClaudeSelfReviewAgent(config, fixer)

        review_json = json.dumps({"findings": []})
        result = agent([], 2, tmp_path, 1, review_json)

        assert result == "sub-1: ok"
        mock_subloop.assert_called_once()


# ── _BudgetFn ────────────────────────────────────────────────────────


class TestBudgetFn:
    @patch("mr_overkill.agents.wait_for_budget", return_value=True)
    def test_uses_explicit_max_wait(
        self,
        mock_wait: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config(retry_max_wait=9999)
        fn = _BudgetFn(config)

        fn("claude", BudgetScope.MICRO, 300)

        _, _, _, actual_wait = mock_wait.call_args[0]
        assert actual_wait == 300

    @patch("mr_overkill.agents.wait_for_budget", return_value=True)
    def test_falls_back_to_config_when_zero(
        self,
        mock_wait: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config(retry_max_wait=9999)
        fn = _BudgetFn(config)

        fn("codex", BudgetScope.MODULE, 0)

        _, _, _, actual_wait = mock_wait.call_args[0]
        assert actual_wait == 9999


# ── Factory functions ────────────────────────────────────────────────


class TestFactories:
    def test_create_review_agent_default(
        self, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config()
        agent = create_review_agent(config)
        assert isinstance(agent, CodexReviewAgent)
        assert isinstance(agent, ReviewAgent)

    def test_create_review_agent_with_scope(
        self, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config()
        agent = create_review_agent(config, scope="module")
        assert isinstance(agent, CodexRefactorReviewAgent)
        assert isinstance(agent, ReviewAgent)

    def test_create_review_agent_claude(
        self, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(reviewer_backend="claude")
        agent = create_review_agent(config)
        assert isinstance(agent, ClaudeReviewAgent)
        assert isinstance(agent, ReviewAgent)

    def test_create_review_agent_claude_with_scope(
        self, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(reviewer_backend="claude")
        agent = create_review_agent(config, scope="module")
        assert isinstance(agent, ClaudeRefactorReviewAgent)
        assert isinstance(agent, ReviewAgent)

    def test_create_fix_agent_default(
        self, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config()
        agent = create_fix_agent(config)
        assert isinstance(agent, ClaudeFixAgent)
        assert isinstance(agent, FixAgent)

    def test_create_fix_agent_refactor(
        self, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config()
        agent = create_fix_agent(config, variant="refactor")
        assert isinstance(agent, ClaudeFixAgent)
        assert isinstance(agent, FixAgent)
        # Verify refactor prompts are set
        assert "refactor" in agent._opinion_prompt

    def test_create_fix_agent_unknown_variant(
        self, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        """Unknown variant falls back to default review fixer."""
        config = make_loop_config()
        agent = create_fix_agent(config, variant="nonsense")
        assert isinstance(agent, ClaudeFixAgent)
        assert agent._opinion_prompt == "claude-fix.prompt.md"

    def test_create_review_agent_gemini(
        self, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(reviewer_backend="gemini")
        agent = create_review_agent(config)
        assert isinstance(agent, GeminiReviewAgent)
        assert isinstance(agent, ReviewAgent)

    def test_create_review_agent_gemini_with_scope(
        self, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(reviewer_backend="gemini")
        agent = create_review_agent(config, scope="module")
        assert isinstance(agent, GeminiRefactorReviewAgent)
        assert isinstance(agent, ReviewAgent)

    def test_create_self_review_agent(
        self, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config()
        fixer = create_fix_agent(config)
        agent = create_self_review_agent(config, fixer)
        assert isinstance(agent, ClaudeSelfReviewAgent)
        assert isinstance(agent, SelfReviewAgent)
