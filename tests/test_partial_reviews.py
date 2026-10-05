"""A failed reviewer leaves the others' findings in play but blocks all_clear."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from mr_overkill.agents import ParallelReviewAgent
from mr_overkill.loop_engine import review_fix_loop
from mr_overkill.models import FinalStatus, LoopConfig, ResumeState
from mr_overkill.retry import retry_codex_cmd

HANG = [sys.executable, "-c", "import time; time.sleep(60)"]


def _config(tmp_path: Path, **kw: object) -> LoopConfig:
    return LoopConfig(
        "feat/x",
        "develop",
        1,
        log_dir=tmp_path,
        reviewer_backend="gemini,claude,codex",
        **kw,  # type: ignore[arg-type]
    )


def _review(findings: list[str]) -> dict[str, object]:
    return {
        "findings": [{"title": title} for title in findings],
        "overall_correctness": "patch is incorrect" if findings else "patch is correct",
    }


def _reviewers(results: dict[str, list[str] | None]) -> MagicMock:
    """Child reviewer factory; None means that reviewer fails."""

    def factory(child: LoopConfig, **kw: object) -> MagicMock:
        def run(output_path: Path, iteration: int) -> bool:
            findings = results[child.reviewer_backend]
            if findings is None:
                return False
            output_path.write_text(json.dumps(_review(findings)))
            return True

        return MagicMock(side_effect=run)

    return MagicMock(side_effect=factory)


@contextmanager
def _loop_env(factory: MagicMock) -> Iterator[None]:
    with ExitStack() as stack:
        for target, value in [
            ("_reject_dirty_worktree", []),
            ("_validate_target_branch", True),
            ("_no_diff", False),
            ("_save_metadata", None),
            ("diff_hash", "hash"),
            ("stash_allowlisted", False),
            ("snapshot_worktree", []),
        ]:
            stack.enter_context(
                patch(f"mr_overkill.loop_engine.{target}", return_value=value)
            )
        stack.enter_context(patch("mr_overkill.agents.create_review_agent", factory))
        yield


def test_survivors_all_clear_is_incomplete_not_all_clear(tmp_path: Path) -> None:
    config = _config(tmp_path)
    fixer = MagicMock()
    with _loop_env(_reviewers({"gemini": None, "claude": [], "codex": []})):
        result = review_fix_loop(
            config,
            reviewer=ParallelReviewAgent(config),
            fixer=fixer,
            cwd=tmp_path,
        )

    assert result.final_status == FinalStatus.REVIEW_INCOMPLETE
    fixer.assert_not_called()
    summary = (tmp_path / "summary.md").read_text()
    assert "**Final status**: review_incomplete" in summary
    assert "missing reviewers: gemini (failed)" in summary


def test_survivors_findings_still_reach_the_fixer(tmp_path: Path) -> None:
    config = _config(tmp_path, auto_commit=False)
    fixer = MagicMock(return_value=True)
    with _loop_env(_reviewers({"gemini": None, "claude": ["a"], "codex": ["b"]})):
        result = review_fix_loop(
            config,
            reviewer=ParallelReviewAgent(config),
            fixer=fixer,
            cwd=tmp_path,
        )

    assert result.final_status == FinalStatus.AUTO_COMMIT_DISABLED
    fixer.assert_called_once()
    review = json.loads(fixer.call_args.args[0])
    assert [f["title"] for f in review["findings"]] == ["a", "b"]
    assert review["missing_reviewers"] == [{"reviewer": "gemini", "reason": "failed"}]


def test_every_reviewer_failing_is_still_review_failed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with _loop_env(_reviewers({"gemini": None, "claude": None, "codex": None})):
        result = review_fix_loop(
            config,
            reviewer=ParallelReviewAgent(config),
            fixer=MagicMock(),
            cwd=tmp_path,
        )

    assert result.final_status == FinalStatus.REVIEW_FAILED


def test_a_timed_out_reviewer_is_named_as_such(tmp_path: Path) -> None:
    config = _config(tmp_path, reviewer_timeout=1)

    def factory(child: LoopConfig, **kw: object) -> MagicMock:
        def run(output_path: Path, iteration: int) -> bool:
            if child.reviewer_backend == "gemini":
                return retry_codex_cmd(output_path.with_suffix(".stderr"), "g", HANG)
            output_path.write_text(json.dumps(_review(["x"])))
            return True

        return MagicMock(side_effect=run)

    output = tmp_path / "review-1.json"
    with patch("mr_overkill.agents.create_review_agent", side_effect=factory):
        assert ParallelReviewAgent(config)(output, 1)

    assert json.loads(output.read_text())["missing_reviewers"] == [
        {"reviewer": "gemini", "reason": "timed out after 1s"},
    ]


def test_resume_does_not_reuse_an_incomplete_review(tmp_path: Path) -> None:
    config = LoopConfig(
        "feat/x",
        "develop",
        1,
        log_dir=tmp_path,
        resume=True,
        dry_run=True,
    )
    (tmp_path / "branch.txt").write_text("feat/x")
    (tmp_path / "target-branch.txt").write_text("develop")
    (tmp_path / "diff-hash-1.txt").write_text("hash")
    (tmp_path / "reviewer-backend.txt").write_text(config.reviewer_backend)
    (tmp_path / "review-1.json").write_text(
        json.dumps(
            {
                **_review([]),
                "missing_reviewers": [{"reviewer": "gemini", "reason": "failed"}],
            }
        )
    )

    def reviewer(output_path: Path, iteration: int) -> bool:
        output_path.write_text(json.dumps(_review([])))
        return True

    reviewer_mock = MagicMock(side_effect=reviewer)
    with (
        _loop_env(MagicMock()),
        patch(
            "mr_overkill.loop_engine.detect_state",
            return_value=ResumeState("resumable", resume_from=1, reuse_review=True),
        ),
    ):
        result = review_fix_loop(
            config,
            reviewer=reviewer_mock,
            fixer=MagicMock(),
            cwd=tmp_path,
        )

    reviewer_mock.assert_called_once()
    assert result.final_status == FinalStatus.ALL_CLEAR
