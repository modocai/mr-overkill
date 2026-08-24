"""Tests for mr_overkill.review_loop — entry point wiring."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill.models import FinalStatus, LoopConfig, LoopResult
from mr_overkill.review_loop import (
    _prepare_commit_scope,
    _prepare_wip_scope,
    run,
)


class TestReviewLoopRun:
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_success_returns_zero(
        self,
        mock_loop: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=1,
            summary_path=tmp_path / "summary.md",
        )
        config = make_loop_config()
        assert run(config) == 0

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_dry_run_returns_zero(
        self,
        mock_loop: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.DRY_RUN,
            iterations_run=1,
        )
        config = make_loop_config(dry_run=True)
        assert run(config) == 0

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_error_returns_one(
        self,
        mock_loop: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.CLAUDE_ERROR,
            iterations_run=0,
        )
        config = make_loop_config()
        assert run(config) == 1

    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_max_iterations_returns_zero(
        self,
        mock_loop: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.MAX_ITERATIONS_REACHED,
            iterations_run=3,
        )
        config = make_loop_config()
        assert run(config) == 0


class TestCITriggerCommit:
    """`--ci-trigger-mode last-only` emits one empty CI trigger commit on PASS."""

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_trigger_commit_emitted_on_all_clear_last_only(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
            made_skipped_fix_commit=True,
        )
        config = make_loop_config(ci_trigger_mode="last-only")
        run(config)
        mock_trigger.assert_called_once_with(branch=config.current_branch)

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_when_no_skipped_fix_commit(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        """ALL_CLEAR without any [skip ci] fix commit (clean branch / resume
        of completed run) must not emit a trigger commit."""
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=1,
            made_skipped_fix_commit=False,
        )
        config = make_loop_config(ci_trigger_mode="last-only")
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_in_every_mode(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
        )
        config = make_loop_config(ci_trigger_mode="every")
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_in_none_mode(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
        )
        config = make_loop_config(ci_trigger_mode="none")
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_on_max_iterations(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.MAX_ITERATIONS_REACHED,
            iterations_run=5,
        )
        config = make_loop_config(ci_trigger_mode="last-only")
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_on_error(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.CLAUDE_ERROR,
            iterations_run=1,
        )
        config = make_loop_config(ci_trigger_mode="last-only")
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_with_no_auto_commit(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
        )
        config = make_loop_config(
            ci_trigger_mode="last-only", auto_commit=False,
        )
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_no_trigger_commit_in_dry_run(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
        )
        config = make_loop_config(
            ci_trigger_mode="last-only", dry_run=True,
        )
        run(config)
        mock_trigger.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    def test_trigger_commit_push_failure_does_not_raise(
        self,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        """A failed CI trigger push should not crash the run() exit path."""
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
            made_skipped_fix_commit=True,
        )
        mock_trigger.side_effect = RuntimeError("git push failed: no remote")
        config = make_loop_config(ci_trigger_mode="last-only")
        # Should still return 0 since the loop itself succeeded.
        assert run(config) == 0


class TestPrepareCommitScope:
    """The preamble that turns a historical commit into something the
    existing loop can run against."""

    SHA = "a" * 40

    def _config(
        self,
        make_loop_config: Callable[..., LoopConfig],
        tmp_path: Path,
        **kw: object,
    ) -> LoopConfig:
        return make_loop_config(
            scope_commit=self.SHA,
            scope_diff_file=tmp_path / "logs" / "scope.diff",
            log_dir=tmp_path / "logs",
            **kw,
        )

    @patch("mr_overkill.review_loop.commit_scope")
    @patch("mr_overkill.review_loop._reject_dirty_worktree", return_value=[])
    def test_creates_branch_and_scope_diff(
        self,
        mock_dirty: MagicMock,
        mock_cs: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_cs.write_scope_diff.return_value = 1234
        mock_cs.review_branch_name.return_value = "review/aaaaaaa-20260101-000000"
        mock_cs.create_branch_at_head.return_value = True
        mock_cs.is_ancestor_of_head.return_value = True
        mock_cs.is_merge_commit.return_value = False

        config = self._config(make_loop_config, tmp_path)
        assert _prepare_commit_scope(config) is True
        assert config.current_branch == "review/aaaaaaa-20260101-000000"
        assert config.skip_initial_no_diff is True
        mock_cs.write_scope_diff.assert_called_once()

    @patch("mr_overkill.review_loop.commit_scope")
    @patch("mr_overkill.review_loop._reject_dirty_worktree", return_value=[])
    def test_dry_run_creates_no_branch(
        self,
        mock_dirty: MagicMock,
        mock_cs: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_cs.write_scope_diff.return_value = 1234
        mock_cs.is_ancestor_of_head.return_value = True
        mock_cs.is_merge_commit.return_value = False

        config = self._config(make_loop_config, tmp_path, dry_run=True)
        before = config.current_branch
        assert _prepare_commit_scope(config) is True
        mock_cs.create_branch_at_head.assert_not_called()
        assert config.current_branch == before
        assert config.skip_initial_no_diff is True

    @patch("mr_overkill.review_loop.commit_scope")
    @patch("mr_overkill.review_loop._reject_dirty_worktree", return_value=[])
    def test_empty_scope_diff_aborts_before_branching(
        self,
        mock_dirty: MagicMock,
        mock_cs: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_cs.write_scope_diff.return_value = 0
        config = self._config(make_loop_config, tmp_path)
        assert _prepare_commit_scope(config) is False
        mock_cs.create_branch_at_head.assert_not_called()

    @patch("mr_overkill.review_loop.commit_scope")
    @patch("mr_overkill.review_loop._reject_dirty_worktree", return_value=["wip.py"])
    def test_dirty_worktree_aborts(
        self,
        mock_dirty: MagicMock,
        mock_cs: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = self._config(make_loop_config, tmp_path)
        assert _prepare_commit_scope(config) is False
        mock_cs.write_scope_diff.assert_not_called()

    @patch("mr_overkill.review_loop.commit_scope")
    @patch("mr_overkill.review_loop._reject_dirty_worktree", return_value=[])
    def test_branch_creation_failure_aborts(
        self,
        mock_dirty: MagicMock,
        mock_cs: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_cs.write_scope_diff.return_value = 10
        mock_cs.review_branch_name.return_value = "review/x"
        mock_cs.create_branch_at_head.return_value = False
        mock_cs.is_ancestor_of_head.return_value = True
        mock_cs.is_merge_commit.return_value = False
        config = self._config(make_loop_config, tmp_path)
        assert _prepare_commit_scope(config) is False

    @patch("mr_overkill.review_loop.commit_scope")
    @patch("mr_overkill.review_loop._reject_dirty_worktree", return_value=[])
    def test_non_ancestor_warns_but_proceeds(
        self,
        mock_dirty: MagicMock,
        mock_cs: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_cs.write_scope_diff.return_value = 10
        mock_cs.review_branch_name.return_value = "review/x"
        mock_cs.create_branch_at_head.return_value = True
        mock_cs.is_ancestor_of_head.return_value = False
        mock_cs.is_merge_commit.return_value = False
        config = self._config(make_loop_config, tmp_path)
        assert _prepare_commit_scope(config) is True

    def test_resume_requires_review_branch(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        config = self._config(
            make_loop_config, tmp_path, resume=True, current_branch="main"
        )
        assert _prepare_commit_scope(config) is False

    @patch("mr_overkill.review_loop.commit_scope")
    @patch("mr_overkill.review_loop._reject_dirty_worktree", return_value=["wip.py"])
    def test_resume_leaves_the_dirty_check_to_the_loop(
        self,
        mock_dirty: MagicMock,
        mock_cs: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        """An interrupted fix leaves partial edits behind; the loop's resume
        reset clears them, so rejecting here would block resume entirely."""
        mock_cs.write_scope_diff.return_value = 10
        mock_cs.is_ancestor_of_head.return_value = True
        mock_cs.is_merge_commit.return_value = False
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            current_branch="review/aaaaaaa-20260101-000000",
        )
        assert _prepare_commit_scope(config) is True
        mock_cs.create_branch_at_head.assert_not_called()

    def test_normal_mode_removes_stale_scope_diff(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        stale = log_dir / "scope.diff"
        stale.write_text("from a previous commit-scope run\n")
        config = make_loop_config(log_dir=log_dir)
        assert _prepare_commit_scope(config) is True
        assert not stale.exists()


class TestCommitScopeRunWiring:
    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    @patch("mr_overkill.review_loop._prepare_commit_scope", return_value=False)
    def test_failed_preamble_returns_one(
        self,
        mock_prep: MagicMock,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        assert run(make_loop_config()) == 1
        mock_loop.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.review_fix_loop")
    @patch("mr_overkill.review_loop._prepare_commit_scope", return_value=True)
    def test_no_ci_trigger_commit_in_commit_scope(
        self,
        mock_prep: MagicMock,
        mock_loop: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
            made_skipped_fix_commit=True,
        )
        config = make_loop_config(
            scope_commit="a" * 40, ci_trigger_mode="last-only"
        )
        assert run(config) == 0
        mock_trigger.assert_not_called()


class TestPrepareWipScope:
    """One flag, two mechanisms: a worktree diff when the run may not commit,
    a scaffolding commit when it may."""

    def _config(
        self,
        make_loop_config: Callable[..., LoopConfig],
        tmp_path: Path,
        **kw: object,
    ) -> LoopConfig:
        config = make_loop_config(wip=True, log_dir=tmp_path / "logs", **kw)
        if config.resume:
            # A resume identifies its scaffolding by the branch it was made
            # on, which the interrupted run recorded here.
            config.log_dir.mkdir(parents=True, exist_ok=True)
            (config.log_dir / "branch.txt").write_text(config.current_branch)
        return config

    def _resumable_logs(self, config: LoopConfig) -> None:
        """Make ``detect_state`` see an interrupted run worth resuming."""
        config.log_dir.mkdir(parents=True, exist_ok=True)
        (config.log_dir / "review-1.json").write_text("{}")

    @patch("mr_overkill.review_loop.wip_scope")
    def test_non_wip_run_clears_a_stale_artefact(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        config = make_loop_config(log_dir=tmp_path / "logs")
        stale = config.log_dir / "wip.diff"
        stale.write_text("from a previous run")

        assert _prepare_wip_scope(config) is True
        assert not stale.exists()
        mock_ws.uncommitted_files.assert_not_called()

    @patch("mr_overkill.review_loop.wip_scope")
    def test_clean_tree_refuses(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_ws.uncommitted_files.return_value = []
        config = self._config(make_loop_config, tmp_path, dry_run=True)

        assert _prepare_wip_scope(config) is False
        mock_ws.create_scaffold_commit.assert_not_called()

    @patch("mr_overkill.review_loop.wip_scope")
    def test_dry_run_captures_a_worktree_diff_instead_of_committing(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.write_worktree_diff.return_value = 512
        config = self._config(make_loop_config, tmp_path, dry_run=True)

        assert _prepare_wip_scope(config) is True
        assert config.scope_diff_file == config.log_dir / "wip.diff"
        assert config.skip_initial_no_diff is True
        mock_ws.create_scaffold_commit.assert_not_called()

    @patch("mr_overkill.review_loop.wip_scope")
    def test_a_previous_runs_fixes_diff_survives_a_no_commit_run(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # Only a committing run writes it, and only at the very end, so a
        # dry-run produces no replacement.  Dropping it here would destroy the
        # only record separating the previous loop's fixes from the author's
        # own edits, in exchange for nothing.
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.write_worktree_diff.return_value = 512
        config = self._config(make_loop_config, tmp_path, dry_run=True)
        config.log_dir.mkdir(parents=True, exist_ok=True)
        stale = config.log_dir / "wip-fixes.diff"
        stale.write_text("--- a previous run's fixes\n")

        assert _prepare_wip_scope(config) is True
        assert stale.read_text() == "--- a previous run's fixes\n"

    @patch("mr_overkill.review_loop.wip_scope")
    def test_no_auto_commit_also_takes_the_diff_path(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.write_worktree_diff.return_value = 512
        config = self._config(make_loop_config, tmp_path, auto_commit=False)

        assert _prepare_wip_scope(config) is True
        mock_ws.create_scaffold_commit.assert_not_called()

    @patch("mr_overkill.review_loop.wip_scope")
    def test_empty_worktree_diff_aborts(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.write_worktree_diff.return_value = 0
        config = self._config(make_loop_config, tmp_path, dry_run=True)

        assert _prepare_wip_scope(config) is False

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_committing_run_scaffolds_and_records_the_base(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.operation_in_progress.return_value = None
        mock_ws.find_scaffold_in_head.return_value = None
        mock_resolve.return_value = "b" * 40
        mock_ws.create_scaffold_commit.return_value = "c" * 40
        config = self._config(make_loop_config, tmp_path)

        assert _prepare_wip_scope(config) is True
        assert config.wip_base == "b" * 40
        assert config.wip_scaffold == "c" * 40
        # The loop's own diff machinery sees the work as a real commit, so
        # nothing needs to be skipped or handed over as an artefact.
        assert config.scope_diff_file is None
        assert config.skip_initial_no_diff is False
        mock_ws.write_worktree_diff.assert_not_called()

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_failed_scaffold_aborts(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.operation_in_progress.return_value = None
        mock_resolve.return_value = "b" * 40
        mock_ws.create_scaffold_commit.return_value = None
        config = self._config(make_loop_config, tmp_path)

        assert _prepare_wip_scope(config) is False
        assert config.wip_base is None

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_unfinished_merge_blocks_scaffolding(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # The scaffolding commit would conclude the merge, and unwinding
        # would then reset past it.
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.operation_in_progress.return_value = "merge"
        config = self._config(make_loop_config, tmp_path)

        assert _prepare_wip_scope(config) is False
        mock_ws.create_scaffold_commit.assert_not_called()

    @patch("mr_overkill.review_loop.wip_scope")
    def test_resume_reuses_the_existing_scaffold(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_ws.operation_in_progress.return_value = None
        mock_ws.is_in_head.return_value = True
        mock_ws.foreign_commits_since.return_value = []
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )
        assert _prepare_wip_scope(config) is True
        mock_ws.create_scaffold_commit.assert_not_called()

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_fresh_run_refuses_to_nest_on_leftover_scaffolding(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # A run killed before its unwind leaves the scaffolding in HEAD, and a
        # fresh run reads no metadata back.  Scaffolding on top of it would make
        # the leftover commit this run's base, overwrite wip-base.txt with it,
        # and unwind only as far as it — stranding the earlier draft in a commit
        # on the branch while the loop reports the tree merely dirty.
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.operation_in_progress.return_value = None
        mock_ws.find_scaffold_in_head.return_value = "e" * 40
        mock_resolve.return_value = "b" * 40
        config = self._config(make_loop_config, tmp_path)

        assert _prepare_wip_scope(config) is False
        mock_ws.create_scaffold_commit.assert_not_called()
        mock_ws.save_metadata.assert_not_called()

    @patch("mr_overkill.review_loop.wip_scope")
    def test_resume_refuses_when_a_foreign_commit_sits_on_the_scaffold(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # The unwind resets to the base, so a normal commit the user made on
        # top of an interrupted run would be rewound into uncommitted changes
        # — the branch check alone does not prove the range is the loop's.
        mock_ws.operation_in_progress.return_value = None
        mock_ws.is_in_head.return_value = True
        mock_ws.foreign_commits_since.return_value = ["feat: my own work"]
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )

        assert _prepare_wip_scope(config) is False
        mock_ws.create_scaffold_commit.assert_not_called()

    @patch("mr_overkill.review_loop.wip_scope")
    def test_resume_on_another_branch_refuses(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # The scaffolding commit is in the history of every branch cut from
        # it, so the ancestry test below would pass here and the unwind would
        # reset this branch's own commits into uncommitted changes.
        mock_ws.is_in_head.return_value = True
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )
        (config.log_dir / "branch.txt").write_text("feat/somewhere-else")

        assert _prepare_wip_scope(config) is False
        mock_ws.create_scaffold_commit.assert_not_called()

    @patch("mr_overkill.review_loop.wip_scope")
    def test_resume_with_the_scaffold_in_head_still_checks_for_a_merge(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # This resume commits without scaffolding again, so the guard has to
        # sit ahead of it: a fix commit would conclude the merge, and the
        # unwind's reset would then drop MERGE_HEAD.
        mock_ws.operation_in_progress.return_value = "merge"
        mock_ws.is_in_head.return_value = True
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )

        assert _prepare_wip_scope(config) is False

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_resume_may_repark_onto_leftover_scaffolding(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # The refusal above is what --resume is pointed at, so it must not fire
        # here: a leftover scaffolding commit is exactly what the re-park path
        # legitimately builds on once it has proved HEAD is the recorded base.
        mock_ws.is_in_head.return_value = False
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.operation_in_progress.return_value = None
        mock_ws.find_scaffold_in_head.return_value = "e" * 40
        mock_resolve.return_value = "b" * 40
        mock_ws.create_scaffold_commit.return_value = "d" * 40
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )
        self._resumable_logs(config)

        assert _prepare_wip_scope(config) is True
        mock_ws.create_scaffold_commit.assert_called_once()

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_resume_rescaffolds_when_the_scaffold_is_gone(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # A soft failure unwinds the scaffolding but leaves wip-base.txt
        # behind, so the work is uncommitted again by the time --resume runs.
        # Taking the metadata at face value would let the loop's resume reset
        # stash it away instead of reviewing it.
        mock_ws.is_in_head.return_value = False
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.operation_in_progress.return_value = None
        mock_resolve.return_value = "b" * 40
        mock_ws.create_scaffold_commit.return_value = "d" * 40
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )
        self._resumable_logs(config)
        assert _prepare_wip_scope(config) is True
        mock_ws.create_scaffold_commit.assert_called_once()
        # Re-parking from the same base keeps loop_engine's resume check happy.
        assert config.wip_base == "b" * 40
        assert config.wip_scaffold == "d" * 40

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_a_rescaffold_records_the_new_sha(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # loop_engine only saves metadata on fresh runs, so if this path does
        # not write the SHA itself, wip-scaffold.txt keeps naming the dangling
        # commit — and a run killed before its unwind leaves the work parked
        # in a commit nothing on disk records.
        mock_ws.is_in_head.return_value = False
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.operation_in_progress.return_value = None
        mock_resolve.return_value = "b" * 40
        mock_ws.create_scaffold_commit.return_value = "d" * 40
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )
        self._resumable_logs(config)
        assert _prepare_wip_scope(config) is True
        mock_ws.save_metadata.assert_called_once_with(
            config.log_dir, "b" * 40, "d" * 40
        )

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_a_rescaffold_keeps_the_previous_attempts_fixes(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # This run's unwind overwrites wip-fixes.diff, and re-parking folded
        # the earlier fixes in with the user's own work, so the old diff is
        # the only remaining record of what that attempt changed.
        mock_ws.is_in_head.return_value = False
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.operation_in_progress.return_value = None
        mock_resolve.return_value = "b" * 40
        mock_ws.create_scaffold_commit.return_value = "d" * 40
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )
        self._resumable_logs(config)
        (config.log_dir / "wip-fixes.diff").write_text("--- attempt 1's fixes\n")

        assert _prepare_wip_scope(config) is True
        assert not (config.log_dir / "wip-fixes.diff").exists()
        kept = config.log_dir / f"wip-fixes-{'c' * 7}.diff"
        assert kept.read_text() == "--- attempt 1's fixes\n"

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_a_finished_run_is_not_reparked(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # A run that finished normally also unwinds its scaffolding and also
        # leaves the metadata behind, so the dangling SHA looks exactly like
        # an interrupted run's. Re-parking here would sweep the tree into a
        # commit the loop short-circuits past and then unwind it again, for a
        # command that used to be a no-op.
        mock_ws.operation_in_progress.return_value = None
        mock_ws.is_in_head.return_value = False
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_resolve.return_value = "b" * 40
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )
        config.log_dir.mkdir(parents=True, exist_ok=True)
        (config.log_dir / "summary.md").write_text("**Final status**: all_clear\n")
        (config.log_dir / "wip-fixes.diff").write_text("--- the finished run\n")

        assert _prepare_wip_scope(config) is True
        mock_ws.create_scaffold_commit.assert_not_called()
        assert (config.log_dir / "wip-fixes.diff").read_text() == (
            "--- the finished run\n"
        )
        # The metadata has to survive for loop_engine's resume check, so the
        # unwind is disarmed instead: the recorded scaffolding is already gone
        # from HEAD, and if the user has since committed their work an unwind
        # would fail the run and point them at a reset that drops that commit.
        assert config.wip_unwind is False

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_an_aborted_repark_leaves_the_fixes_diff_alone(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # The artefact is only doomed once the new scaffolding exists. Bailing
        # out before that and still having renamed it would break the command
        # the README documents on a run that did nothing.
        mock_ws.is_in_head.return_value = False
        mock_ws.uncommitted_files.return_value = ["a.py"]
        mock_ws.operation_in_progress.return_value = "merge"
        mock_resolve.return_value = "b" * 40
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )
        self._resumable_logs(config)
        (config.log_dir / "wip-fixes.diff").write_text("--- attempt 1's fixes\n")

        assert _prepare_wip_scope(config) is False
        assert (config.log_dir / "wip-fixes.diff").read_text() == (
            "--- attempt 1's fixes\n"
        )
        assert not list(config.log_dir.glob("wip-fixes-*.diff"))

    @patch("mr_overkill.review_loop.commit_scope.resolve_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    def test_a_clean_tree_while_rescaffolding_points_at_the_base(
        self,
        mock_ws: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Nothing uncommitted and nothing in the recorded scaffolding means an
        # earlier run was killed before it could unwind: the work sits in a
        # commit of its own and only the base leads back to it.
        mock_ws.operation_in_progress.return_value = None
        mock_ws.is_in_head.return_value = False
        mock_ws.uncommitted_files.return_value = []
        mock_resolve.return_value = "e" * 40
        config = self._config(
            make_loop_config,
            tmp_path,
            resume=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )
        self._resumable_logs(config)
        assert _prepare_wip_scope(config) is False
        assert f"git reset --mixed {'b' * 40}" in caplog.text

    @patch("mr_overkill.review_loop.wip_scope")
    def test_resume_without_a_recorded_base_refuses(
        self,
        mock_ws: MagicMock,
        tmp_path: Path,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # Scaffolding again would stack a second throwaway commit and lose
        # the only record of where to unwind to.
        config = self._config(make_loop_config, tmp_path, resume=True)
        assert _prepare_wip_scope(config) is False
        mock_ws.create_scaffold_commit.assert_not_called()


class TestWipUnwind:
    @patch("mr_overkill.review_loop.wip_scope")
    @patch("mr_overkill.review_loop.review_fix_loop")
    @patch("mr_overkill.review_loop._prepare_wip_scope", return_value=True)
    def test_scaffolding_comes_down_after_a_normal_run(
        self,
        mock_prep: MagicMock,
        mock_loop: MagicMock,
        mock_ws: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR, iterations_run=1
        )
        mock_ws.unwind.return_value = True
        config = make_loop_config(wip=True, wip_base="b" * 40, wip_scaffold="c" * 40)

        assert run(config) == 0
        mock_ws.unwind.assert_called_once_with(
            "b" * 40, "c" * 40, config.log_dir, branch=config.current_branch
        )

    @patch("mr_overkill.review_loop.wip_scope")
    @patch("mr_overkill.review_loop.review_fix_loop")
    @patch("mr_overkill.review_loop._prepare_wip_scope", return_value=True)
    def test_scaffolding_comes_down_when_the_loop_raises(
        self,
        mock_prep: MagicMock,
        mock_loop: MagicMock,
        mock_ws: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # A crash that left the scaffolding in place would be
        # indistinguishable from commits the author meant to keep.
        mock_loop.side_effect = RuntimeError("boom")
        mock_ws.unwind.return_value = True
        config = make_loop_config(wip=True, wip_base="b" * 40, wip_scaffold="c" * 40)

        with pytest.raises(RuntimeError):
            run(config)
        mock_ws.unwind.assert_called_once()

    @patch("mr_overkill.review_loop.wip_scope")
    @patch("mr_overkill.review_loop.review_fix_loop")
    @patch("mr_overkill.review_loop._prepare_wip_scope", return_value=True)
    def test_a_failed_unwind_fails_the_run(
        self,
        mock_prep: MagicMock,
        mock_loop: MagicMock,
        mock_ws: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # Exiting 0 would tell the caller no commits were left behind when
        # some demonstrably were.
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR, iterations_run=1
        )
        mock_ws.unwind.return_value = False
        config = make_loop_config(wip=True, wip_base="b" * 40, wip_scaffold="c" * 40)

        assert run(config) == 1

    @patch("mr_overkill.review_loop.wip_scope")
    @patch("mr_overkill.review_loop.review_fix_loop")
    @patch("mr_overkill.review_loop._prepare_wip_scope", return_value=True)
    def test_nothing_to_unwind_when_this_run_scaffolded_nothing(
        self,
        mock_prep: MagicMock,
        mock_loop: MagicMock,
        mock_ws: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # A --wip --resume that found nothing to resume keeps the recorded base
        # for the resume check but made no scaffolding of its own.
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR, iterations_run=0
        )
        config = make_loop_config(
            wip=True, wip_base="b" * 40, wip_scaffold="c" * 40, wip_unwind=False
        )

        assert run(config) == 0
        mock_ws.unwind.assert_not_called()

    @patch("mr_overkill.review_loop.wip_scope")
    @patch("mr_overkill.review_loop.review_fix_loop")
    @patch("mr_overkill.review_loop._prepare_wip_scope", return_value=True)
    def test_nothing_to_unwind_when_the_run_may_not_commit(
        self,
        mock_prep: MagicMock,
        mock_loop: MagicMock,
        mock_ws: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        # --resume restores wip-base.txt whatever the commit mode, so a dry run
        # starts with another run's metadata. Unwinding it would refuse over —
        # or reset away — a commit this run never made.
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.DRY_RUN, iterations_run=1
        )
        config = make_loop_config(
            wip=True,
            resume=True,
            dry_run=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
        )

        assert run(config) == 0
        mock_ws.unwind.assert_not_called()

    @patch("mr_overkill.review_loop.wip_scope")
    @patch("mr_overkill.review_loop.review_fix_loop")
    @patch("mr_overkill.review_loop._prepare_wip_scope", return_value=True)
    def test_nothing_to_unwind_without_a_scaffold(
        self,
        mock_prep: MagicMock,
        mock_loop: MagicMock,
        mock_ws: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.DRY_RUN, iterations_run=1
        )
        config = make_loop_config(wip=True, dry_run=True)

        assert run(config) == 0
        mock_ws.unwind.assert_not_called()

    @patch("mr_overkill.review_loop.push_trigger_commit")
    @patch("mr_overkill.review_loop.wip_scope")
    @patch("mr_overkill.review_loop.review_fix_loop")
    @patch("mr_overkill.review_loop._prepare_wip_scope", return_value=True)
    def test_no_ci_trigger_commit_in_wip_mode(
        self,
        mock_prep: MagicMock,
        mock_loop: MagicMock,
        mock_ws: MagicMock,
        mock_trigger: MagicMock,
        make_loop_config: Callable[..., LoopConfig],
    ) -> None:
        mock_loop.return_value = LoopResult(
            final_status=FinalStatus.ALL_CLEAR,
            iterations_run=2,
            made_skipped_fix_commit=True,
        )
        mock_ws.unwind.return_value = True
        config = make_loop_config(
            wip=True,
            wip_base="b" * 40,
            wip_scaffold="c" * 40,
            ci_trigger_mode="last-only",
        )
        assert run(config) == 0
        mock_trigger.assert_not_called()
