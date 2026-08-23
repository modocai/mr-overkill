"""Tests for mr_overkill.loop_engine — unified review-fix loop."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from mr_overkill.loop_engine import _save_metadata, review_fix_loop
from mr_overkill.models import FinalStatus, LoopConfig, ResumeState


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


class TestLoopEngineCITriggerSuffix:
    """`ci_trigger_mode` controls whether iteration commits carry [skip ci]."""

    @staticmethod
    def _commit_subject(mock_commit: MagicMock) -> str:
        # commit_and_push(snapshot, message, branch, cwd=...)
        message: str = mock_commit.call_args.args[1]
        return message.splitlines()[0]

    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine.commit_and_push", return_value=True)
    @patch("mr_overkill.loop_engine.unstash_allowlisted", return_value=True)
    @patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False)
    @patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_every_mode_no_skip_marker(
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
        config = make_loop_config(max_loop=2, ci_trigger_mode="every")
        reviewer = _mock_reviewer([
            {
                "findings": [{"title": "Bug"}],
                "overall_correctness": "patch is incorrect",
            },
            {"findings": [], "overall_correctness": "patch is correct"},
        ])
        review_fix_loop(
            config, reviewer=reviewer, fixer=MagicMock(return_value=True),
            cwd=tmp_path,
        )
        subject = self._commit_subject(mock_commit)
        assert "[skip ci]" not in subject
        assert subject == "fix(ai-review): apply iteration 1 fixes"

    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine.commit_and_push", return_value=True)
    @patch("mr_overkill.loop_engine.unstash_allowlisted", return_value=True)
    @patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False)
    @patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_last_only_mode_appends_skip_marker(
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
        config = make_loop_config(max_loop=2, ci_trigger_mode="last-only")
        reviewer = _mock_reviewer([
            {
                "findings": [{"title": "Bug"}],
                "overall_correctness": "patch is incorrect",
            },
            {"findings": [], "overall_correctness": "patch is correct"},
        ])
        review_fix_loop(
            config, reviewer=reviewer, fixer=MagicMock(return_value=True),
            cwd=tmp_path,
        )
        subject = self._commit_subject(mock_commit)
        assert subject.endswith(" [skip ci]")
        assert subject == "fix(ai-review): apply iteration 1 fixes [skip ci]"

    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine.commit_and_push", return_value=True)
    @patch("mr_overkill.loop_engine.unstash_allowlisted", return_value=True)
    @patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False)
    @patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def test_none_mode_appends_skip_marker(
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
        config = make_loop_config(max_loop=2, ci_trigger_mode="none")
        reviewer = _mock_reviewer([
            {
                "findings": [{"title": "Bug"}],
                "overall_correctness": "patch is incorrect",
            },
            {"findings": [], "overall_correctness": "patch is correct"},
        ])
        review_fix_loop(
            config, reviewer=reviewer, fixer=MagicMock(return_value=True),
            cwd=tmp_path,
        )
        subject = self._commit_subject(mock_commit)
        assert subject.endswith(" [skip ci]")


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
    @patch("mr_overkill.loop_engine.git_all_dirty", return_value=[])
    @patch("mr_overkill.loop_engine._no_diff", return_value=True)
    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine.subprocess.run")
    def test_metadata_saved(
        self,
        mock_run: MagicMock,
        mock_dirty: MagicMock,
        mock_diff: MagicMock,
        _mock_dirty: MagicMock,
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


class TestCommitScopeLoopBehaviour:
    """Loop-side effects of commit-scope: local-only pushes, scope metadata,
    and a no-op fix being an outcome rather than an error."""

    @patch("mr_overkill.loop_engine.commit_and_push", return_value=True)
    @patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False)
    @patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[])
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    @patch("mr_overkill.loop_engine._no_diff", return_value=False)
    @patch("mr_overkill.loop_engine._save_metadata")
    def _run_one_fix(
        self,
        mock_save: MagicMock,
        mock_diff: MagicMock,
        mock_validate: MagicMock,
        mock_dirty: MagicMock,
        mock_snap: MagicMock,
        mock_stash: MagicMock,
        mock_commit: MagicMock,
        *,
        config: LoopConfig,
        tmp_path: Path,
    ) -> MagicMock:
        review = {
            "findings": [{"title": "P2 something", "body": "b"}],
            "overall_correctness": "patch is incorrect",
        }
        review_fix_loop(
            config,
            reviewer=_mock_reviewer([review, review]),
            fixer=MagicMock(return_value=True),
            cwd=tmp_path,
        )
        return mock_commit

    def test_push_branch_false_suppresses_push(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        """An empty branch argument routes commit_and_push to its
        "no upstream — skipping push" path, keeping review/* local."""
        config = make_loop_config(
            max_loop=1, log_dir=tmp_path, push_branch=False,
            current_branch="review/aaaaaaa-1",
        )
        mock_commit = self._run_one_fix(config=config, tmp_path=tmp_path)
        assert mock_commit.call_args.args[2] == ""

    def test_push_branch_true_passes_branch(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(
            max_loop=1, log_dir=tmp_path, current_branch="feat/x",
        )
        mock_commit = self._run_one_fix(config=config, tmp_path=tmp_path)
        assert mock_commit.call_args.args[2] == "feat/x"


class TestCommitScopeResumeGuard:
    @patch("mr_overkill.loop_engine.detect_state")
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    def test_scope_mismatch_rejected(
        self,
        mock_validate: MagicMock,
        mock_detect: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        """Resuming a commit-scope run as a plain review would reuse logs that
        describe a different review scope entirely."""
        mock_detect.return_value = ResumeState(
            status="resumable", resume_from=2, reuse_review=False
        )
        (tmp_path / "branch.txt").write_text("review/aaaaaaa-1")
        (tmp_path / "target-branch.txt").write_text("main")
        (tmp_path / "scope-commit.txt").write_text("a" * 40)

        config = make_loop_config(
            resume=True, dry_run=True, log_dir=tmp_path,
            current_branch="review/aaaaaaa-1", target_branch="main",
        )
        result = review_fix_loop(
            config, reviewer=MagicMock(), fixer=MagicMock(), cwd=tmp_path
        )
        assert result.final_status == FinalStatus.REVIEW_FAILED

    @patch("mr_overkill.loop_engine.detect_state")
    @patch("mr_overkill.loop_engine._validate_target_branch", return_value=True)
    def test_matching_scope_accepted(
        self,
        mock_validate: MagicMock,
        mock_detect: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_detect.return_value = ResumeState(
            status="completed", resume_from=2, reuse_review=False,
            prev_status="all_clear",
        )
        (tmp_path / "branch.txt").write_text("review/aaaaaaa-1")
        (tmp_path / "target-branch.txt").write_text("main")
        (tmp_path / "scope-commit.txt").write_text("a" * 40)

        config = make_loop_config(
            resume=True, dry_run=True, log_dir=tmp_path,
            current_branch="review/aaaaaaa-1", target_branch="main",
            scope_commit="a" * 40,
        )
        result = review_fix_loop(
            config, reviewer=MagicMock(), fixer=MagicMock(), cwd=tmp_path
        )
        assert result.final_status != FinalStatus.REVIEW_FAILED


class TestSaveMetadataScope:
    def test_writes_scope_commit(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(log_dir=tmp_path, scope_commit="a" * 40)
        _save_metadata(config, tmp_path)
        assert (tmp_path / "scope-commit.txt").read_text() == "a" * 40

    def test_omits_when_not_commit_scope(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        _save_metadata(make_loop_config(log_dir=tmp_path), tmp_path)
        assert not (tmp_path / "scope-commit.txt").exists()

    def test_clears_stale_scope_commit(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        """A plain run must not leave a prior commit-scope run's marker
        behind, or a later --resume would restore an unrelated scope."""
        (tmp_path / "scope-commit.txt").write_text("a" * 40)
        _save_metadata(make_loop_config(log_dir=tmp_path), tmp_path)
        assert not (tmp_path / "scope-commit.txt").exists()


class TestCommitScopeNoFixOutcome:
    """A fixer that changes nothing is an error for a PR, but a legitimate
    result when reviewing history — a later commit may already have fixed it."""

    def _run(
        self,
        config: LoopConfig,
        tmp_path: Path,
    ) -> FinalStatus:
        review = {
            "findings": [{"title": "P2 something", "body": "b"}],
            "overall_correctness": "patch is incorrect",
        }
        clean = {"findings": [], "overall_correctness": "patch is correct"}
        with (
            patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[]),
            patch("mr_overkill.loop_engine._validate_target_branch", return_value=True),
            patch("mr_overkill.loop_engine._save_metadata"),
            patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False),
            patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[]),
            patch("mr_overkill.loop_engine.commit_and_push", return_value=False),
            patch("mr_overkill.loop_engine._no_diff", side_effect=[False, True]),
        ):
            result = review_fix_loop(
                config,
                reviewer=_mock_reviewer([review, clean]),
                fixer=MagicMock(return_value=True),
                cwd=tmp_path,
            )
        return result.final_status

    def test_commit_scope_reports_no_diff(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(
            max_loop=2, log_dir=tmp_path, scope_commit="a" * 40,
            skip_initial_no_diff=True,
        )
        assert self._run(config, tmp_path) == FinalStatus.NO_DIFF

    def test_normal_mode_still_reports_claude_error(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = make_loop_config(max_loop=2, log_dir=tmp_path)
        assert self._run(config, tmp_path) == FinalStatus.CLAUDE_ERROR
