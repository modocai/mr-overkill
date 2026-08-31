"""Tests for workspace_policy — the paths overkill owns inside a project.

The point of this module is that three separate questions used to be answered
by three hand-maintained copies of the same list, and the copies had drifted.
So most of what is worth asserting here is not "does the constant hold value
X" but "do the call sites still agree with each other".
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from mr_overkill import (
    git_ops,
    init,
    refactor_suggest,
    wip_scope,
    workspace_policy,
)
from mr_overkill.loop_engine import review_fix_loop
from mr_overkill.models import LoopConfig


def _writes(review: dict[str, object]) -> Callable[..., bool]:
    """A reviewer stub that writes *review* to the file it is handed."""

    def reviewer(output: Path, _iteration: int) -> bool:
        output.write_text(json.dumps(review))
        return True

    return reviewer


class TestAllowlist:
    def test_every_rc_name_is_covered_under_every_layout(self) -> None:
        # The drift this module exists to prevent: a config file protected
        # under one of its names but not another.
        for name in (
            workspace_policy.RC_NAME,
            workspace_policy.LEGACY_RC_NAME,
            workspace_policy.REFACTOR_RC_NAME,
        ):
            assert name in workspace_policy.ALLOWLISTED_FILES
            for directory in (
                workspace_policy.WORKSPACE_DIR,
                workspace_policy.LEGACY_WORKSPACE_DIR,
            ):
                assert f"{directory}/{name}" in workspace_policy.ALLOWLISTED_FILES

    def test_gitignore_is_allowlisted(self) -> None:
        # ``overkill init`` writes to it, so a run must not refuse over it.
        assert ".gitignore" in workspace_policy.ALLOWLISTED_FILES

    def test_source_files_are_not_tool_owned(self) -> None:
        for path in ("src/mr_overkill/cli.py", "README.md", ".overkillrc.example"):
            assert not workspace_policy.is_tool_owned(path)

    def test_refactor_suggest_shares_the_one_allowlist(self) -> None:
        assert set(refactor_suggest._ALLOWLISTED_FILES) == (
            workspace_policy.ALLOWLISTED_FILES
        )

    def test_the_loop_stashes_exactly_what_it_tolerates_dirty(
        self, tmp_path: Path, make_loop_config: Callable[..., LoopConfig]
    ) -> None:
        """The two lists that had drifted must stay the same list.

        The dirty check lets a file be dirty; the stash list keeps the fixer
        from committing it. A path in one but not the other is protected in
        name only — which is exactly what had happened.
        """
        review = {
            "findings": [{"title": "P2 x", "body": "b"}],
            "overall_correctness": "patch is incorrect",
        }
        stash = MagicMock(return_value=False)
        with (
            patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[]),
            patch("mr_overkill.loop_engine._validate_target_branch", return_value=True),
            patch("mr_overkill.loop_engine._save_metadata"),
            patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[]),
            patch("mr_overkill.loop_engine.commit_and_push", return_value=True),
            patch("mr_overkill.loop_engine._no_diff", return_value=False),
            patch("mr_overkill.loop_engine.stash_allowlisted", stash),
        ):
            review_fix_loop(
                make_loop_config(max_loop=1, log_dir=tmp_path),
                reviewer=_writes(review),
                fixer=MagicMock(return_value=True),
                cwd=tmp_path,
            )

        stash.assert_called_once()
        assert set(stash.call_args[0][0]) == workspace_policy.ALLOWLISTED_FILES


class TestLogArtefacts:
    def test_both_layouts_are_recognised(self) -> None:
        assert workspace_policy.is_log_artefact(".overkill/logs/review-1.json")
        assert workspace_policy.is_log_artefact(".review-loop/logs/summary.md")

    def test_the_workspace_itself_is_not_a_log_artefact(self) -> None:
        # Prompts and config live beside the logs but are not run output.
        assert not workspace_policy.is_log_artefact(".overkill/prompts/active/x.md")
        assert not workspace_policy.is_log_artefact(".overkill/.overkillrc")

    def test_a_lookalike_path_outside_the_workspace_is_not_matched(self) -> None:
        assert not workspace_policy.is_log_artefact("src/.overkill/logs/x.json")
        assert not workspace_policy.is_log_artefact("logs/review-1.json")

    def test_wip_scope_shares_the_one_tuple(self) -> None:
        assert wip_scope._LOG_PREFIXES is workspace_policy.LOG_PREFIXES

    def test_commit_and_push_leaves_log_artefacts_out(
        self, tmp_git_repo: Path
    ) -> None:
        # The exclusion that keeps a run's own output from being committed
        # into the branch it is reviewing.
        logs = tmp_git_repo / workspace_policy.WORKSPACE_DIR / "logs"
        logs.mkdir(parents=True)
        (logs / "review-1.json").write_text("{}")
        (tmp_git_repo / "real.py").write_text("real = 1\n")

        assert git_ops.commit_and_push([], "test: c", push=False, cwd=tmp_git_repo)

        committed = subprocess.run(
            ["git", "show", "--name-only", "--format=", "HEAD"],
            cwd=tmp_git_repo, capture_output=True, text=True, check=True,
        ).stdout.split()
        assert committed == ["real.py"]


class TestNonAllowlisted:
    def test_keeps_the_author_s_files_and_drops_the_tool_s(self) -> None:
        dirty = [
            "src/mr_overkill/cli.py",
            ".gitignore",
            ".overkill/logs/review-1.json",
            ".review-loop/.reviewlooprc",
            "README.md",
        ]
        assert workspace_policy.non_allowlisted(dirty) == [
            "src/mr_overkill/cli.py",
            "README.md",
        ]

    def test_order_is_preserved(self) -> None:
        # The result is what the error message names, so it should read in the
        # order git reported.
        dirty = ["z.py", "a.py", "m.py"]
        assert workspace_policy.non_allowlisted(dirty) == dirty

    def test_clean_tree(self) -> None:
        assert workspace_policy.non_allowlisted([]) == []


class TestWorkspacePaths:
    def test_current_layout_comes_first(self, tmp_path: Path) -> None:
        current, legacy = workspace_policy.workspace_paths(tmp_path, "logs")
        assert current == tmp_path / workspace_policy.WORKSPACE_DIR / "logs"
        assert legacy == tmp_path / workspace_policy.LEGACY_WORKSPACE_DIR / "logs"

    def test_nested_parts(self) -> None:
        current, _ = workspace_policy.workspace_paths(
            Path("/repo"), "logs", "refactor"
        )
        assert current == Path("/repo/.overkill/logs/refactor")


class TestInitSharesTheConstants:
    def test_directory_names(self) -> None:
        assert init._OVERKILL_DIR == workspace_policy.WORKSPACE_DIR
        assert init._LEGACY_DIR == workspace_policy.LEGACY_WORKSPACE_DIR

    def test_gitignore_marker_follows_the_workspace_dir(
        self, tmp_path: Path
    ) -> None:
        init._ensure_gitignore(tmp_path)

        written = (tmp_path / ".gitignore").read_text()
        assert f"{workspace_policy.WORKSPACE_DIR}/" in written
