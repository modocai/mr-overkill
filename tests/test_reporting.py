"""Tests for mr_overkill.reporting — summary generation and PR comments."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mr_overkill.models import FinalStatus
from mr_overkill.reporting import generate_summary, post_pr_comment


class TestGenerateSummary:
    def test_basic_summary(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        result = generate_summary(
            title="Review Loop Summary",
            log_dir=log_dir,
            current_branch="feat/test",
            target_branch="develop",
            max_loop=3,
            final_status=FinalStatus.ALL_CLEAR,
        )

        assert result == log_dir / "summary.md"
        content = result.read_text()
        assert "# Review Loop Summary" in content
        assert "feat/test → develop" in content
        assert "**Max iterations**: 3" in content
        assert "**Final status**: all_clear" in content

    def test_includes_iteration_findings(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        review = {
            "findings": [{"title": "Bug A"}, {"title": "Bug B"}],
            "overall_correctness": "patch is incorrect",
        }
        (log_dir / "review-1.json").write_text(json.dumps(review))

        result = generate_summary(
            title="Summary",
            log_dir=log_dir,
            current_branch="feat/x",
            target_branch="main",
            max_loop=1,
            final_status=FinalStatus.MAX_ITERATIONS_REACHED,
        )

        content = result.read_text()
        assert "**Iteration 1**: 2 findings" in content
        assert "patch is incorrect" in content

    def test_includes_self_review_sub_iterations(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        (log_dir / "review-1.json").write_text(
            json.dumps({"findings": [], "overall_correctness": "ok"})
        )
        (log_dir / "self-review-1-1.json").write_text(
            json.dumps({
                "findings": [{"title": "nit"}],
                "overall_correctness": "patch is incorrect",
            })
        )
        (log_dir / "self-review-1-2.json").write_text(
            json.dumps({
                "findings": [],
                "overall_correctness": "patch is correct",
            })
        )

        result = generate_summary(
            title="Summary",
            log_dir=log_dir,
            current_branch="feat/x",
            target_branch="main",
            max_loop=1,
            final_status=FinalStatus.ALL_CLEAR,
        )

        content = result.read_text()
        assert "Sub-iteration 1: 1 findings" in content
        assert "Sub-iteration 2: 0 findings" in content

    def test_extra_lines(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        result = generate_summary(
            title="Refactor Summary",
            log_dir=log_dir,
            current_branch="refactor/x",
            target_branch="develop",
            max_loop=1,
            final_status=FinalStatus.DRY_RUN,
            extra_lines=["- **Scope**: module"],
        )

        content = result.read_text()
        assert "- **Scope**: module" in content


class TestPostPrComment:
    def test_no_pr_number(self) -> None:
        assert post_pr_comment("", 1, 3, {"findings": []}) is False

    @patch("mr_overkill.reporting.subprocess.run")
    def test_successful_post(self, mock_run: object, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        mock_run_fn = MagicMock(return_value=MagicMock(returncode=0))
        with patch("mr_overkill.reporting.subprocess.run", mock_run_fn):
            result = post_pr_comment(
                pr_number="42",
                iteration=1,
                max_loop=3,
                review_json={
                    "findings": [
                        {
                            "title": "Bug",
                            "confidence_score": 0.9,
                            "code_location": {
                                "file_path": "src/foo.py",
                                "line_range": {"start": 10},
                            },
                        }
                    ],
                    "overall_correctness": "patch is incorrect",
                },
            )
            assert result is True
            mock_run_fn.assert_called_once()
