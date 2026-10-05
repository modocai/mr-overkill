"""A fresh run moves the previous run's logs aside instead of deleting them."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mr_overkill.loop_engine import (
    LOG_HISTORY_DIR,
    LOG_HISTORY_KEEP,
    _clean_stale_logs,
    _history_key,
)


def _previous_run(log_dir: Path) -> None:
    (log_dir / "review-1.json").write_text("{}")
    (log_dir / "review-1.stderr").write_text("codex stderr")
    (log_dir / "review-1.diff").write_text("gemini evidence")
    (log_dir / "summary.md").write_text("old summary")
    (log_dir / "diff-1-1.diff").write_text("self-review diff")
    gemini = log_dir / "reviewers" / "gemini"
    gemini.mkdir(parents=True)
    (gemini / "review-1.stderr").write_text("HTTP 500, retrying")


def _runs(log_dir: Path) -> list[Path]:
    return sorted((log_dir / LOG_HISTORY_DIR).iterdir())


def test_previous_run_is_archived_not_deleted(tmp_path: Path) -> None:
    _previous_run(tmp_path)
    # Written by the CLI for the run that is starting; must stay put.
    (tmp_path / "scope.diff").write_text("current scope")
    (tmp_path / "branch.txt").write_text("feat/x")

    _clean_stale_logs(tmp_path)

    [run] = _runs(tmp_path)
    assert (run / "reviewers" / "gemini" / "review-1.stderr").read_text() == (
        "HTTP 500, retrying"
    )
    for name in (
        "review-1.json",
        "review-1.stderr",
        "review-1.diff",
        "summary.md",
        "diff-1-1.diff",
    ):
        assert (run / name).is_file()
        assert not (tmp_path / name).exists()
    assert not (tmp_path / "reviewers").exists()
    assert (tmp_path / "scope.diff").read_text() == "current scope"
    assert (tmp_path / "branch.txt").is_file()


def test_nothing_to_archive_creates_no_history(tmp_path: Path) -> None:
    (tmp_path / "scope.diff").write_text("current scope")

    _clean_stale_logs(tmp_path)

    assert not (tmp_path / LOG_HISTORY_DIR).exists()


def test_runs_in_the_same_second_do_not_collide(tmp_path: Path) -> None:
    for _ in range(2):
        _previous_run(tmp_path)
        _clean_stale_logs(tmp_path)

    assert len(_runs(tmp_path)) == 2


def test_only_the_newest_runs_are_kept(tmp_path: Path) -> None:
    history = tmp_path / LOG_HISTORY_DIR
    for day in range(1, LOG_HISTORY_KEEP + 2):
        (history / f"202601{day:02d}T000000Z").mkdir(parents=True)

    _previous_run(tmp_path)
    _clean_stale_logs(tmp_path)

    runs = [run.name for run in _runs(tmp_path)]
    assert len(runs) == LOG_HISTORY_KEEP
    assert "20260101T000000Z" not in runs
    assert "20260102T000000Z" not in runs


def test_same_second_runs_stay_chronological_past_the_limit(tmp_path: Path) -> None:
    runs = LOG_HISTORY_KEEP + 7
    for i in range(runs):
        _previous_run(tmp_path)
        (tmp_path / "review-1.json").write_text(str(i))
        with patch("mr_overkill.loop_engine.datetime") as clock:
            clock.now.return_value.strftime.return_value = "20261005T000000Z"
            _clean_stale_logs(tmp_path)

    kept = [(run / "review-1.json").read_text() for run in _runs_in_order(tmp_path)]
    assert kept == [str(i) for i in range(runs - LOG_HISTORY_KEEP, runs)]


def _runs_in_order(log_dir: Path) -> list[Path]:
    return sorted(_runs(log_dir), key=_history_key)
