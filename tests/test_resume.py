"""Tests for mr_overkill.resume — resume state detection."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mr_overkill.resume import detect_state


def _make_log_dir(tmp_path: Path) -> Path:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return log_dir


class TestDetectState:
    def test_no_logs(self, tmp_path: Path) -> None:
        log_dir = _make_log_dir(tmp_path)
        state = detect_state(log_dir, "fix(ai-review): apply iteration")
        assert state.status == "no_logs"
        assert state.resume_from == 1
        assert state.reuse_review is False

    def test_completed_all_clear(self, tmp_path: Path) -> None:
        log_dir = _make_log_dir(tmp_path)
        (log_dir / "summary.md").write_text(
            "# Summary\n- **Final status**: all_clear\n"
        )
        state = detect_state(log_dir, "fix(ai-review): apply iteration")
        assert state.status == "completed"
        assert state.prev_status == "all_clear"

    def test_completed_max_iterations(self, tmp_path: Path) -> None:
        log_dir = _make_log_dir(tmp_path)
        (log_dir / "summary.md").write_text(
            "# Summary\n- **Final status**: max_iterations_reached\n"
        )
        state = detect_state(log_dir, "fix(ai-review): apply iteration")
        assert state.status == "completed"
        assert state.prev_status == "max_iterations_reached"

    def test_completed_dry_run(self, tmp_path: Path) -> None:
        log_dir = _make_log_dir(tmp_path)
        (log_dir / "summary.md").write_text(
            "# Summary\n- **Final status**: dry_run\n"
        )
        state = detect_state(log_dir, "fix(ai-review): apply iteration")
        assert state.status == "completed"
        assert state.prev_status == "dry_run"

    def test_resumable_with_valid_review(self, tmp_git_repo: Path) -> None:
        log_dir = tmp_git_repo / "logs"
        log_dir.mkdir()
        review = {"findings": [], "overall_correctness": "patch is correct"}
        (log_dir / "review-2.json").write_text(json.dumps(review))
        # No commit exists → reuse_review=True
        state = detect_state(log_dir, "fix(ai-review): apply iteration", cwd=tmp_git_repo)
        assert state.status == "resumable"
        assert state.resume_from == 2
        assert state.reuse_review is True

    def test_resumable_with_invalid_review(self, tmp_git_repo: Path) -> None:
        log_dir = tmp_git_repo / "logs"
        log_dir.mkdir()
        (log_dir / "review-3.json").write_text("not valid json!!!")
        state = detect_state(log_dir, "fix(ai-review): apply iteration", cwd=tmp_git_repo)
        assert state.status == "resumable"
        assert state.resume_from == 3
        assert state.reuse_review is False

    def test_resumable_commit_found(self, tmp_git_repo: Path) -> None:
        log_dir = tmp_git_repo / "logs"
        log_dir.mkdir()
        review = {"findings": [{"title": "bug"}]}
        (log_dir / "review-1.json").write_text(json.dumps(review))

        # Record start commit
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        (log_dir / "start-commit.txt").write_text(head)

        # Make a commit that matches the pattern
        (tmp_git_repo / "fix.txt").write_text("fixed")
        subprocess.run(
            ["git", "add", "fix.txt"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "fix(ai-review): apply iteration 1 fixes"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        state = detect_state(
            log_dir, "fix(ai-review): apply iteration", cwd=tmp_git_repo
        )
        assert state.status == "resumable"
        assert state.resume_from == 2
        assert state.reuse_review is False

    def test_completed_past_max_loop(self, tmp_git_repo: Path) -> None:
        log_dir = tmp_git_repo / "logs"
        log_dir.mkdir()
        review = {"findings": []}
        (log_dir / "review-3.json").write_text(json.dumps(review))
        (log_dir / "max-loop.txt").write_text("3")

        # Record start commit
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        (log_dir / "start-commit.txt").write_text(head)

        # Make a commit for iteration 3
        (tmp_git_repo / "fix.txt").write_text("fixed")
        subprocess.run(
            ["git", "add", "fix.txt"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "fix(ai-review): apply iteration 3 fixes"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        state = detect_state(
            log_dir, "fix(ai-review): apply iteration", cwd=tmp_git_repo
        )
        assert state.status == "completed"
        assert state.prev_status == "max_iterations_reached"

    def test_picks_highest_iteration(self, tmp_git_repo: Path) -> None:
        log_dir = tmp_git_repo / "logs"
        log_dir.mkdir()
        (log_dir / "review-1.json").write_text('{"findings":[]}')
        (log_dir / "review-5.json").write_text('{"findings":[]}')
        (log_dir / "review-3.json").write_text('{"findings":[]}')
        state = detect_state(log_dir, "fix(ai-review): apply iteration", cwd=tmp_git_repo)
        assert state.resume_from == 5  # highest
