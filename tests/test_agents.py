"""Tests for mr_overkill.agents — ABC implementations and factory functions."""

from __future__ import annotations

import json
import string
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
    _budget_check,
    _BudgetFn,
    _format_review_scope,
    _render_review_prompt,
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

        # Structured output: schema flag must point at the bundled review schema.
        assert "--output-schema" in cmd_args
        schema_path = cmd_args[cmd_args.index("--output-schema") + 1]
        assert Path(schema_path).name == "review.schema.json"
        assert Path(schema_path).is_file()

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

        # Structured output: --output-format json + --json-schema with the
        # bundled review schema text inlined into argv.
        cmd_args = mock_retry.call_args[0][2]
        assert "--output-format" in cmd_args
        assert cmd_args[cmd_args.index("--output-format") + 1] == "json"
        assert "--json-schema" in cmd_args
        schema_text = cmd_args[cmd_args.index("--json-schema") + 1]
        schema = json.loads(schema_text)
        assert schema["title"] == "ReviewResult"
        assert "findings" in schema["properties"]

    def test_unwraps_claude_structured_output(
        self,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        """Wrapper produced by ``--output-format json`` is unwrapped on disk.

        Regression for the Claude CLI emitting
        ``{"type":"result", ..., "structured_output": {<review>}}`` — the
        downstream parser expects the review object, not the wrapper.
        """
        review = {
            "findings": [],
            "overall_correctness": "patch is correct",
            "overall_confidence_score": 0.9,
        }
        wrapper = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Done.",
            "structured_output": review,
        }

        prompts = tmp_path / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / "claude-review.prompt.md").write_text("prompt body")
        config = make_loop_config(prompts_dir=prompts)

        output = tmp_path / "review.json"

        def fake_retry(
            out_path: Path,
            _label: str,
            _argv: list[str],
            **_kw: object,
        ) -> bool:
            out_path.write_text(json.dumps(wrapper))
            return True

        with (
            patch("mr_overkill.agents._make_budget_fn") as mb,
            patch("mr_overkill.agents._make_retry_fn") as mr,
        ):
            mb.return_value = MagicMock(return_value=True)
            mr.return_value = fake_retry
            agent = ClaudeReviewAgent(config)
            assert agent(output, 1) is True

        assert json.loads(output.read_text()) == review

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

    @patch("mr_overkill.agents.wait_for_budget", return_value=False)
    def test_skip_budget_gate_bypasses_wait(
        self,
        mock_wait: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config(skip_budget_gate=True)
        fn = _BudgetFn(config)

        assert fn("codex", BudgetScope.MICRO, 0) is True
        mock_wait.assert_not_called()


class TestBudgetCheckEnvOverride:
    @patch("mr_overkill.agents.codex_budget_sufficient", return_value=False)
    def test_env_var_bypasses_check(
        self,
        mock_codex: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OVERKILL_SKIP_BUDGET", "1")

        assert _budget_check("codex", BudgetScope.MICRO, 0) is True
        mock_codex.assert_not_called()

    @patch("mr_overkill.agents.codex_budget_sufficient", return_value=False)
    def test_check_runs_without_env_var(
        self,
        mock_codex: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("OVERKILL_SKIP_BUDGET", raising=False)

        assert _budget_check("codex", BudgetScope.MICRO, 0) is False
        mock_codex.assert_called_once()


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


class TestReviewScopeNote:
    """`${REVIEW_SCOPE_NOTE}` is empty in normal mode and load-bearing in
    commit-scope mode."""

    def _prompt(self, tmp_path: Path, marker: bool = True) -> Path:
        p = tmp_path / "codex-review.prompt.md"
        note = "${REVIEW_SCOPE_NOTE}\n" if marker else ""
        p.write_text(
            "You are a code reviewer analyzing a proposed change.\n"
            f"{note}"
            "## Context\n\n"
            "- Current: ${CURRENT_BRANCH}\n- Target: ${TARGET_BRANCH}\n"
            "- Iteration: ${ITERATION}\n${REVIEWER_CONTEXT}\n"
        )
        return p

    def test_empty_in_normal_mode(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config()
        assert _format_review_scope(config, 1) == ""

    def test_normal_mode_render_is_unchanged(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        """Rendering must be byte-identical to substituting the legacy vars."""
        prompt = self._prompt(tmp_path)
        config = make_loop_config()
        expected = string.Template(prompt.read_text()).safe_substitute({
            "CURRENT_BRANCH": config.current_branch,
            "TARGET_BRANCH": config.target_branch,
            "ITERATION": "1",
            "REVIEWER_CONTEXT": "",
            "REVIEW_SCOPE_NOTE": "",
        })
        assert _render_review_prompt(prompt, config, 1) == expected

    def test_commit_scope_note_is_injected(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(
            scope_commit="a" * 40, scope_diff_file=tmp_path / "scope.diff"
        )
        with (
            patch(
                "mr_overkill.commit_scope.commit_headline",
                return_value="abc — x",
            ),
            patch(
                "mr_overkill.commit_scope.is_ancestor_of_head",
                return_value=True,
            ),
        ):
            rendered = _render_review_prompt(self._prompt(tmp_path), config, 1)
        assert rendered is not None
        assert "REVIEW MODE: COMMIT SCOPE" in rendered
        assert str(tmp_path / "scope.diff") in rendered
        assert "sole authority" in rendered

    def test_iteration_one_says_no_fixes_yet(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(
            scope_commit="a" * 40, scope_diff_file=tmp_path / "scope.diff"
        )
        with (
            patch(
                "mr_overkill.commit_scope.commit_headline",
                return_value="abc — x",
            ),
            patch(
                "mr_overkill.commit_scope.is_ancestor_of_head",
                return_value=True,
            ),
        ):
            note = _format_review_scope(config, 1)
        assert "first pass" in note
        assert "This is iteration" not in note

    def test_later_iteration_describes_prior_fixes(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        """The loop only converges if the reviewer is told the branch diff is
        its own earlier fixes, and given an explicit way to stop."""
        config = make_loop_config(
            scope_commit="a" * 40, scope_diff_file=tmp_path / "scope.diff"
        )
        with (
            patch(
                "mr_overkill.commit_scope.commit_headline",
                return_value="abc — x",
            ),
            patch(
                "mr_overkill.commit_scope.is_ancestor_of_head",
                return_value=True,
            ),
        ):
            note = _format_review_scope(config, 3)
        assert "This is iteration 3" in note
        assert "never re-report a resolved finding" in note.lower()
        assert "patch is correct" in note

    def test_non_ancestor_warning(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(
            scope_commit="a" * 40, scope_diff_file=tmp_path / "scope.diff"
        )
        with (
            patch(
                "mr_overkill.commit_scope.commit_headline",
                return_value="abc — x",
            ),
            patch(
                "mr_overkill.commit_scope.is_ancestor_of_head",
                return_value=False,
            ),
        ):
            note = _format_review_scope(config, 1)
        assert "not an ancestor of HEAD" in note

    def test_stale_prompt_rejected_in_commit_scope(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        """A prompt without the marker would drop the note silently and have
        the reviewer inspect an empty branch diff — a false pass."""
        config = make_loop_config(
            scope_commit="a" * 40, scope_diff_file=tmp_path / "scope.diff"
        )
        prompt = self._prompt(tmp_path, marker=False)
        assert _render_review_prompt(prompt, config, 1) is None

    def test_stale_prompt_accepted_in_normal_mode(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config()
        prompt = self._prompt(tmp_path, marker=False)
        assert isinstance(_render_review_prompt(prompt, config, 1), str)

    def test_shipped_prompts_carry_the_marker(self) -> None:
        root = Path(__file__).resolve().parent.parent / "prompts" / "active"
        for backend in ("codex", "claude", "gemini"):
            text = (root / f"{backend}-review.prompt.md").read_text()
            assert "${REVIEW_SCOPE_NOTE}" in text, backend
