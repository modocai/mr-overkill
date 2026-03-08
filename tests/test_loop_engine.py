"""Tests for mr_overkill.loop_engine — unified review-fix loop."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from mr_overkill.loop_engine import review_fix_loop
from mr_overkill.models import FinalStatus, LoopConfig


def _mock_reviewer(reviews: list[dict[str, object]]) -> MagicMock:
    """Create a mock reviewer that writes review JSON files."""
    call_count = 0

    def reviewer(output_path: Path, iteration: int) -> bool:
        nonlocal call_count
        if call_count < len(reviews):
            output_path.write_text(json.dumps(reviews[call_count]))
            call_count += 1
            return True
        return False

    return MagicMock(side_effect=reviewer)


class TestLoopEngineAllClear:
    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_all_clear_first_iteration(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_validate: MagicMock,
        mock_dirty: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config()
        review = {
            "findings": [],
            "overall_correctness": "patch is correct",
        }
        reviewer = _mock_reviewer([review])
        fixer = MagicMock(return_value=True)

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.final_status == FinalStatus.ALL_CLEAR
        assert result.iterations_run == 1
        fixer.assert_not_called()  # No fix needed


class TestLoopEngineDryRun:
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_dry_run_skips_fixes(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config(dry_run=True)
        review = {
            "findings": [{"title": "Bug"}],
            "overall_correctness": "patch is incorrect",
        }
        reviewer = _mock_reviewer([review])
        fixer = MagicMock(return_value=True)

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.final_status == FinalStatus.DRY_RUN
        fixer.assert_not_called()


class TestLoopEngineNoDiff:
    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine._no_diff", return_value=True)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_no_diff_exits(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_validate: MagicMock,
        mock_dirty: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config()
        reviewer = MagicMock()
        fixer = MagicMock()

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.final_status == FinalStatus.NO_DIFF
        reviewer.assert_not_called()


class TestLoopEngineReviewFailure:
    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_reviewer_failure(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_validate: MagicMock,
        mock_dirty: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config()
        reviewer = MagicMock(return_value=False)
        fixer = MagicMock()

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.final_status == FinalStatus.CODEX_ERROR


class TestLoopEngineFixFlow:
    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine.commit_and_push", return_value=True)
    @patch("mr_overkill.loop_engine.unstash_allowlisted", return_value=True)
    @patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False)
    @patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_fix_then_all_clear(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_snap: MagicMock,
        mock_stash: MagicMock,
        mock_unstash: MagicMock,
        mock_commit: MagicMock,
        mock_validate: MagicMock,
        mock_dirty: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        """Iteration 1: findings + fix, Iteration 2: all clear."""
        config = make_loop_config(max_loop=3)

        reviews = [
            {
                "findings": [{"title": "Bug"}],
                "overall_correctness": "patch is incorrect",
            },
            {
                "findings": [],
                "overall_correctness": "patch is correct",
            },
        ]
        reviewer = _mock_reviewer(reviews)
        fixer = MagicMock(return_value=True)

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.final_status == FinalStatus.ALL_CLEAR
        assert fixer.call_count == 1
        mock_commit.assert_called_once()

    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine.unstash_allowlisted", return_value=True)
    @patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False)
    @patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_fixer_failure(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_snap: MagicMock,
        mock_stash: MagicMock,
        mock_unstash: MagicMock,
        mock_validate: MagicMock,
        mock_dirty: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config()
        review = {
            "findings": [{"title": "Bug"}],
            "overall_correctness": "patch is incorrect",
        }
        reviewer = _mock_reviewer([review])
        fixer = MagicMock(return_value=False)

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.final_status == FinalStatus.CLAUDE_ERROR


class TestLoopEngineMaxIterations:
    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine.commit_and_push", return_value=True)
    @patch("mr_overkill.loop_engine.unstash_allowlisted", return_value=True)
    @patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False)
    @patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_max_iterations_reached(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_snap: MagicMock,
        mock_stash: MagicMock,
        mock_unstash: MagicMock,
        mock_commit: MagicMock,
        mock_validate: MagicMock,
        mock_dirty: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config(max_loop=2)
        review = {
            "findings": [{"title": "Bug"}],
            "overall_correctness": "patch is incorrect",
        }
        # Both iterations have findings
        reviewer = _mock_reviewer([review, review])
        fixer = MagicMock(return_value=True)

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.final_status == FinalStatus.MAX_ITERATIONS_REACHED
        assert fixer.call_count == 2


class TestLoopEngineAutoCommitDisabled:
    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine.unstash_allowlisted", return_value=True)
    @patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False)
    @patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_no_auto_commit_stops_after_one(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_snap: MagicMock,
        mock_stash: MagicMock,
        mock_unstash: MagicMock,
        mock_validate: MagicMock,
        mock_dirty: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config(auto_commit=False, max_loop=3)
        review = {
            "findings": [{"title": "Bug"}],
            "overall_correctness": "patch is incorrect",
        }
        reviewer = _mock_reviewer([review])
        fixer = MagicMock(return_value=True)

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.final_status == FinalStatus.AUTO_COMMIT_DISABLED
        assert fixer.call_count == 1


class TestLoopEngineResume:
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    def test_resume_completed(
        self,
        mock_diff: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config(resume=True)
        log_dir = config.log_dir

        # Write summary indicating completed run
        (log_dir / "summary.md").write_text(
            "# Summary\n- **Final status**: all_clear\n"
        )
        (log_dir / "branch.txt").write_text(config.current_branch)

        reviewer = MagicMock()
        fixer = MagicMock()

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.final_status == FinalStatus.ALL_CLEAR
        assert result.iterations_run == 0
        reviewer.assert_not_called()

    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    def test_resume_no_logs(
        self,
        mock_diff: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config(resume=True)
        # Log dir exists but no review files

        reviewer = MagicMock()
        fixer = MagicMock()

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.final_status == FinalStatus.REVIEW_FAILED
        reviewer.assert_not_called()


class TestLoopEngineSelfReview:
    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine.commit_and_push", return_value=True)
    @patch("mr_overkill.loop_engine.unstash_allowlisted", return_value=True)
    @patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False)
    @patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._no_diff")
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_self_review_called(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_snap: MagicMock,
        mock_stash: MagicMock,
        mock_unstash: MagicMock,
        mock_commit: MagicMock,
        mock_validate: MagicMock,
        mock_dirty: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # First iteration: findings, second: all clear
        mock_diff.side_effect = [False, False]
        config = make_loop_config(max_loop=3, max_subloop=4)
        reviews = [
            {
                "findings": [{"title": "Bug"}],
                "overall_correctness": "patch is incorrect",
            },
            {
                "findings": [],
                "overall_correctness": "patch is correct",
            },
        ]
        reviewer = _mock_reviewer(reviews)
        fixer = MagicMock(return_value=True)
        self_reviewer = MagicMock(return_value="Sub-iteration 1: 0 findings — passed\n")

        result = review_fix_loop(
            config,
            reviewer=reviewer,
            fixer=fixer,
            self_reviewer=self_reviewer,
            cwd=tmp_path,
        )

        assert result.final_status == FinalStatus.ALL_CLEAR
        self_reviewer.assert_called_once()


class TestLoopEngineSummary:
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_summary_generated(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config(dry_run=True)
        review = {
            "findings": [{"title": "Bug"}],
            "overall_correctness": "patch is incorrect",
        }
        reviewer = _mock_reviewer([review])
        fixer = MagicMock()

        result = review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        assert result.summary_path is not None
        assert result.summary_path.is_file()
        content = result.summary_path.read_text()
        assert "dry_run" in content


class TestLoopEngineMetadata:
    @patch("mr_overkill.loop_engine._no_diff", return_value=True)
    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine.subprocess.run")
    def test_metadata_saved(
        self,
        mock_run: MagicMock,
        mock_dirty: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="abc123\n", stderr=""
        )
        config = make_loop_config(max_loop=3)
        reviewer = MagicMock()
        fixer = MagicMock()

        review_fix_loop(config, reviewer=reviewer, fixer=fixer, cwd=tmp_path)

        log_dir = config.log_dir
        assert (log_dir / "branch.txt").read_text() == "feat/test"
        assert (log_dir / "target-branch.txt").read_text() == "develop"
        assert (log_dir / "max-loop.txt").read_text() == "3"
