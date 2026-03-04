"""Tests for mr_overkill.self_review — self-review sub-loop."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mr_overkill.models import WorktreeSnapshot
from mr_overkill.self_review import (
    _build_fix_nits_guidelines,
    _compute_fingerprint,
    self_review_subloop,
)


def _make_prompts(tmp_path: Path) -> Path:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "claude-self-review.prompt.md").write_text(
        "Review: $REVIEW_JSON\nDiff: $DIFF_FILE\n"
        "$EXTRA_REVIEW_GUIDELINES\n"
        "$SELF_REVIEW_HISTORY"
    )
    return prompts


def _make_snapshot() -> list[WorktreeSnapshot]:
    return [
        WorktreeSnapshot(file_hash="abc123", mode="100644", path="src/foo.py"),
    ]


class TestComputeFingerprint:
    def test_empty(self) -> None:
        assert _compute_fingerprint([]) == ""

    def test_deterministic(self) -> None:
        findings = [
            {"title": "Bug", "code_location": {"file_path": "a.py"}, "body": "x"},
            {"title": "Dup", "code_location": {"file_path": "b.py"}, "body": "y"},
        ]
        fp1 = _compute_fingerprint(findings)
        fp2 = _compute_fingerprint(list(reversed(findings)))
        assert fp1 == fp2  # sorted, so order doesn't matter

    def test_different_findings(self) -> None:
        f1 = [{"title": "Bug", "code_location": {"file_path": "a.py"}}]
        f2 = [{"title": "Other", "code_location": {"file_path": "b.py"}}]
        assert _compute_fingerprint(f1) != _compute_fingerprint(f2)


class TestSelfReviewNoChanges:
    @patch(
        "mr_overkill.self_review.changed_files_since_snapshot",
        return_value=[],
    )
    def test_no_changes_skips(
        self, mock_changed: MagicMock, tmp_path: Path
    ) -> None:
        prompts = _make_prompts(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        result = self_review_subloop(
            pre_fix_snapshot=_make_snapshot(),
            max_subloop=4,
            log_dir=log_dir,
            iteration=1,
            review_json_str="{}",
            retry_fn=MagicMock(),
            budget_fn=MagicMock(return_value=True),
            fix_fn=MagicMock(),
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert result == ""


class TestSelfReviewAllClear:
    @patch("mr_overkill.self_review._generate_diff")
    @patch(
        "mr_overkill.self_review.changed_files_since_snapshot",
        return_value=["src/foo.py"],
    )
    def test_all_clear_first_sub(
        self,
        mock_changed: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        prompts = _make_prompts(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Make diff file non-empty
        def write_diff(
            changed: list[str], output: Path, cwd: Path | None
        ) -> None:
            output.write_text("diff --git a/foo b/foo\n")

        mock_diff.side_effect = write_diff

        # retry_fn writes all-clear JSON
        def fake_retry(
            output_path: Path, label: str, cmd_args: list[str], **kw: object
        ) -> bool:
            output_path.write_text(
                json.dumps({
                    "findings": [],
                    "overall_correctness": "patch is correct",
                })
            )
            return True

        result = self_review_subloop(
            pre_fix_snapshot=_make_snapshot(),
            max_subloop=4,
            log_dir=log_dir,
            iteration=1,
            review_json_str="{}",
            retry_fn=fake_retry,
            budget_fn=MagicMock(return_value=True),
            fix_fn=MagicMock(),
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert "0 findings — passed" in result


class TestSelfReviewRefix:
    @patch("mr_overkill.self_review._generate_diff")
    @patch("mr_overkill.self_review.changed_files_since_snapshot")
    def test_findings_then_refix(
        self,
        mock_changed: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        prompts = _make_prompts(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # First call: changed files exist, second: no changes
        mock_changed.side_effect = [["src/foo.py"], []]

        def write_diff(
            changed: list[str], output: Path, cwd: Path | None
        ) -> None:
            output.write_text("diff --git a/foo b/foo\n")

        mock_diff.side_effect = write_diff

        # retry writes findings JSON
        def fake_retry(
            output_path: Path, label: str, cmd_args: list[str], **kw: object
        ) -> bool:
            output_path.write_text(
                json.dumps({
                    "findings": [{"title": "Minor issue"}],
                    "overall_correctness": "patch is incorrect",
                })
            )
            return True

        fix_fn = MagicMock(return_value=True)

        result = self_review_subloop(
            pre_fix_snapshot=_make_snapshot(),
            max_subloop=4,
            log_dir=log_dir,
            iteration=1,
            review_json_str="{}",
            retry_fn=fake_retry,
            budget_fn=MagicMock(return_value=True),
            fix_fn=fix_fn,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert "1 findings — re-fixed" in result
        fix_fn.assert_called_once()


class TestSelfReviewConvergence:
    @patch("mr_overkill.self_review._generate_diff")
    @patch(
        "mr_overkill.self_review.changed_files_since_snapshot",
        return_value=["src/foo.py"],
    )
    def test_same_findings_stops(
        self,
        mock_changed: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        prompts = _make_prompts(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        def write_diff(
            changed: list[str], output: Path, cwd: Path | None
        ) -> None:
            output.write_text("diff --git a/foo b/foo\n")

        mock_diff.side_effect = write_diff

        # Always returns the same finding
        def fake_retry(
            output_path: Path, label: str, cmd_args: list[str], **kw: object
        ) -> bool:
            output_path.write_text(
                json.dumps({
                    "findings": [
                        {
                            "title": "Persistent bug",
                            "code_location": {"file_path": "a.py"},
                            "body": "same issue",
                        }
                    ],
                    "overall_correctness": "patch is incorrect",
                })
            )
            return True

        fix_fn = MagicMock(return_value=True)

        result = self_review_subloop(
            pre_fix_snapshot=_make_snapshot(),
            max_subloop=4,
            log_dir=log_dir,
            iteration=1,
            review_json_str="{}",
            retry_fn=fake_retry,
            budget_fn=MagicMock(return_value=True),
            fix_fn=fix_fn,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert "not converging" in result
        # Should have done 1 re-fix then stopped on iteration 2
        assert fix_fn.call_count == 1


class TestSelfReviewDryRun:
    @patch("mr_overkill.self_review._generate_diff")
    @patch(
        "mr_overkill.self_review.changed_files_since_snapshot",
        return_value=["src/foo.py"],
    )
    def test_dry_run_no_refix(
        self,
        mock_changed: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        prompts = _make_prompts(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        def write_diff(
            changed: list[str], output: Path, cwd: Path | None
        ) -> None:
            output.write_text("diff content\n")

        mock_diff.side_effect = write_diff

        def fake_retry(
            output_path: Path, label: str, cmd_args: list[str], **kw: object
        ) -> bool:
            output_path.write_text(
                json.dumps({
                    "findings": [{"title": "Bug"}],
                    "overall_correctness": "patch is incorrect",
                })
            )
            return True

        fix_fn = MagicMock()

        result = self_review_subloop(
            pre_fix_snapshot=_make_snapshot(),
            max_subloop=4,
            log_dir=log_dir,
            iteration=1,
            review_json_str="{}",
            retry_fn=fake_retry,
            budget_fn=MagicMock(return_value=True),
            fix_fn=fix_fn,
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
            dry_run=True,
        )

        assert "dry-run" in result
        fix_fn.assert_not_called()


class TestSelfReviewBudgetTimeout:
    @patch(
        "mr_overkill.self_review.changed_files_since_snapshot",
        return_value=["src/foo.py"],
    )
    def test_budget_timeout_stops(
        self,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        prompts = _make_prompts(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        result = self_review_subloop(
            pre_fix_snapshot=_make_snapshot(),
            max_subloop=4,
            log_dir=log_dir,
            iteration=1,
            review_json_str="{}",
            retry_fn=MagicMock(),
            budget_fn=MagicMock(return_value=False),
            fix_fn=MagicMock(),
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert result == ""  # No sub-iterations completed


class TestFixNitsGuidelines:
    def test_fix_nits_true_injects_guidelines(self) -> None:
        text = _build_fix_nits_guidelines()
        assert "Fix nits and potential issues" in text
        assert "Strict correctness in fix-nits mode" in text
        assert "patch is incorrect" in text

    @patch("mr_overkill.self_review._generate_diff")
    @patch(
        "mr_overkill.self_review.changed_files_since_snapshot",
        return_value=["src/foo.py"],
    )
    def test_fix_nits_prompt_injection(
        self,
        mock_changed: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        """fix_nits=True should populate EXTRA_REVIEW_GUIDELINES in prompt."""
        prompts = _make_prompts(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        def write_diff(
            changed: list[str], output: Path, cwd: Path | None
        ) -> None:
            output.write_text("diff --git a/foo b/foo\n")

        mock_diff.side_effect = write_diff

        captured_prompt: list[str] = []

        def fake_retry(
            output_path: Path, label: str, cmd_args: list[str], **kw: object
        ) -> bool:
            captured_prompt.append(str(kw.get("stdin", "")))
            output_path.write_text(
                json.dumps({
                    "findings": [],
                    "overall_correctness": "patch is correct",
                })
            )
            return True

        self_review_subloop(
            pre_fix_snapshot=_make_snapshot(),
            max_subloop=4,
            log_dir=log_dir,
            iteration=1,
            review_json_str="{}",
            retry_fn=fake_retry,
            budget_fn=MagicMock(return_value=True),
            fix_fn=MagicMock(),
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
            fix_nits=True,
        )

        assert len(captured_prompt) == 1
        assert "Fix nits and potential issues" in captured_prompt[0]

    @patch("mr_overkill.self_review._generate_diff")
    @patch(
        "mr_overkill.self_review.changed_files_since_snapshot",
        return_value=["src/foo.py"],
    )
    def test_fix_nits_false_empty_guidelines(
        self,
        mock_changed: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        """fix_nits=False (default) should leave EXTRA_REVIEW_GUIDELINES empty."""
        prompts = _make_prompts(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        def write_diff(
            changed: list[str], output: Path, cwd: Path | None
        ) -> None:
            output.write_text("diff --git a/foo b/foo\n")

        mock_diff.side_effect = write_diff

        captured_prompt: list[str] = []

        def fake_retry(
            output_path: Path, label: str, cmd_args: list[str], **kw: object
        ) -> bool:
            captured_prompt.append(str(kw.get("stdin", "")))
            output_path.write_text(
                json.dumps({
                    "findings": [],
                    "overall_correctness": "patch is correct",
                })
            )
            return True

        self_review_subloop(
            pre_fix_snapshot=_make_snapshot(),
            max_subloop=4,
            log_dir=log_dir,
            iteration=1,
            review_json_str="{}",
            retry_fn=fake_retry,
            budget_fn=MagicMock(return_value=True),
            fix_fn=MagicMock(),
            prompts_dir=prompts,
            current_branch="feat/x",
            target_branch="develop",
        )

        assert len(captured_prompt) == 1
        assert "Fix nits" not in captured_prompt[0]
