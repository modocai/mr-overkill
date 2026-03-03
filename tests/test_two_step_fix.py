"""Tests for mr_overkill.two_step_fix — two-step Claude fix."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from mr_overkill.two_step_fix import _render_prompt, claude_two_step_fix


class TestRenderPrompt:
    def test_basic_substitution(self, tmp_path: Path) -> None:
        tmpl = tmp_path / "prompt.md"
        tmpl.write_text("Review $CURRENT_BRANCH vs $TARGET_BRANCH\n$REVIEW_JSON")
        result = _render_prompt(tmpl, {
            "CURRENT_BRANCH": "feat/x",
            "TARGET_BRANCH": "main",
            "REVIEW_JSON": '{"findings":[]}',
        })
        assert "feat/x" in result
        assert "main" in result
        assert '{"findings":[]}' in result

    def test_missing_var_left_as_is(self, tmp_path: Path) -> None:
        tmpl = tmp_path / "prompt.md"
        tmpl.write_text("Branch: $CURRENT_BRANCH, Missing: $UNKNOWN")
        result = _render_prompt(tmpl, {"CURRENT_BRANCH": "feat/x"})
        assert "feat/x" in result
        assert "$UNKNOWN" in result


class TestClaudeTwoStepFix:
    def _make_prompts(self, tmp_path: Path) -> Path:
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "claude-fix.prompt.md").write_text(
            "Fix: $REVIEW_JSON on $CURRENT_BRANCH vs $TARGET_BRANCH $FIX_HISTORY"
        )
        (prompts / "claude-fix-execute.prompt.md").write_text(
            "Execute the plan above."
        )
        return prompts

    def test_success(self, tmp_path: Path) -> None:
        prompts = self._make_prompts(tmp_path)
        opinion = tmp_path / "opinion.md"
        fix = tmp_path / "fix.md"

        retry = MagicMock(return_value=True)
        budget = MagicMock(return_value=True)

        result = claude_two_step_fix(
            review_json='{"findings":[]}',
            opinion_file=opinion,
            fix_file=fix,
            label="fix",
            retry_fn=retry,
            budget_fn=budget,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert result is True
        assert retry.call_count == 2
        assert budget.call_count == 2

        # Check step 1 call
        step1_call = retry.call_args_list[0]
        assert step1_call[0][0] == opinion  # output_path
        assert "opinion" in step1_call[0][1]  # label
        assert "--session-id" in step1_call[0][2]
        assert step1_call[1]["stdin"] is not None  # stdin should be the prompt

        # Check step 2 call
        step2_call = retry.call_args_list[1]
        assert step2_call[0][0] == fix
        assert "--resume" in step2_call[0][2]

    def test_budget_timeout_step1(self, tmp_path: Path) -> None:
        prompts = self._make_prompts(tmp_path)
        retry = MagicMock(return_value=True)
        budget = MagicMock(return_value=False)

        result = claude_two_step_fix(
            review_json="{}",
            opinion_file=tmp_path / "opinion.md",
            fix_file=tmp_path / "fix.md",
            label="fix",
            retry_fn=retry,
            budget_fn=budget,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert result is False
        retry.assert_not_called()

    def test_budget_timeout_step2(self, tmp_path: Path) -> None:
        prompts = self._make_prompts(tmp_path)
        retry = MagicMock(return_value=True)
        # First budget check passes, second fails
        budget = MagicMock(side_effect=[True, False])

        result = claude_two_step_fix(
            review_json="{}",
            opinion_file=tmp_path / "opinion.md",
            fix_file=tmp_path / "fix.md",
            label="fix",
            retry_fn=retry,
            budget_fn=budget,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert result is False
        assert retry.call_count == 1  # Only step 1 ran

    def test_opinion_failure(self, tmp_path: Path) -> None:
        prompts = self._make_prompts(tmp_path)
        # retry fails on step 1
        retry = MagicMock(return_value=False)
        budget = MagicMock(return_value=True)

        result = claude_two_step_fix(
            review_json="{}",
            opinion_file=tmp_path / "opinion.md",
            fix_file=tmp_path / "fix.md",
            label="fix",
            retry_fn=retry,
            budget_fn=budget,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert result is False
        assert retry.call_count == 1

    def test_execute_failure(self, tmp_path: Path) -> None:
        prompts = self._make_prompts(tmp_path)
        # step 1 succeeds, step 2 fails
        retry = MagicMock(side_effect=[True, False])
        budget = MagicMock(return_value=True)

        result = claude_two_step_fix(
            review_json="{}",
            opinion_file=tmp_path / "opinion.md",
            fix_file=tmp_path / "fix.md",
            label="fix",
            retry_fn=retry,
            budget_fn=budget,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert result is False
        assert retry.call_count == 2

    @patch("mr_overkill.two_step_fix.gen_uuid", return_value="test-uuid-1234")
    def test_session_id_shared(self, mock_uuid: MagicMock, tmp_path: Path) -> None:
        prompts = self._make_prompts(tmp_path)
        retry = MagicMock(return_value=True)
        budget = MagicMock(return_value=True)

        claude_two_step_fix(
            review_json="{}",
            opinion_file=tmp_path / "opinion.md",
            fix_file=tmp_path / "fix.md",
            label="fix",
            retry_fn=retry,
            budget_fn=budget,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        # Both steps should use the same session ID
        step1_args = retry.call_args_list[0][0][2]
        step2_args = retry.call_args_list[1][0][2]
        assert "test-uuid-1234" in step1_args
        assert "test-uuid-1234" in step2_args

    def test_custom_prompts(self, tmp_path: Path) -> None:
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "custom-opinion.md").write_text("Custom: $REVIEW_JSON")
        (prompts / "custom-execute.md").write_text("Custom execute")

        retry = MagicMock(return_value=True)
        budget = MagicMock(return_value=True)

        result = claude_two_step_fix(
            review_json="{}",
            opinion_file=tmp_path / "opinion.md",
            fix_file=tmp_path / "fix.md",
            label="fix",
            retry_fn=retry,
            budget_fn=budget,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
            opinion_prompt="custom-opinion.md",
            execute_prompt="custom-execute.md",
        )

        assert result is True
        # Verify custom prompt was used in step 1
        step1_stdin = retry.call_args_list[0][1]["stdin"]
        assert "Custom:" in step1_stdin

    def test_fix_history_injected(self, tmp_path: Path) -> None:
        prompts = self._make_prompts(tmp_path)
        retry = MagicMock(return_value=True)
        budget = MagicMock(return_value=True)

        claude_two_step_fix(
            review_json="{}",
            opinion_file=tmp_path / "opinion.md",
            fix_file=tmp_path / "fix.md",
            label="fix",
            retry_fn=retry,
            budget_fn=budget,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
            fix_history="### Previous attempt\nDid X",
        )

        step1_stdin = retry.call_args_list[0][1]["stdin"]
        assert "Previous attempt" in step1_stdin
