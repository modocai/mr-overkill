"""Tests for mr_overkill.cli — CLI argument parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill.cli import (
    _load_rc_file,
    _resolve_bool,
    parse_review_loop_args,
)


class TestResolveBool:
    def test_flag_true_wins(self) -> None:
        assert _resolve_bool(True, False, "false", False) is True

    def test_flag_false_wins(self) -> None:
        assert _resolve_bool(None, True, "true", True) is False

    def test_rc_value(self) -> None:
        assert _resolve_bool(None, False, "true", False) is True
        assert _resolve_bool(None, False, "false", True) is False

    def test_default(self) -> None:
        assert _resolve_bool(None, False, None, True) is True
        assert _resolve_bool(None, False, None, False) is False


class TestLoadRcFile:
    @patch("mr_overkill.cli.subprocess.run")
    def test_no_git_root(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _load_rc_file(".reviewlooprc") == {}

    @patch("mr_overkill.cli.subprocess.run")
    def test_parses_valid_keys(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=str(tmp_path)
        )
        rc = tmp_path / ".reviewlooprc"
        rc.write_text(
            "# comment\n"
            "TARGET_BRANCH=main\n"
            "MAX_LOOP=5\n"
            "DRY_RUN=true\n"
            'BUDGET_SCOPE="module"\n'
            "INVALID_KEY=ignored\n"
        )
        result = _load_rc_file(".reviewlooprc")
        assert result["TARGET_BRANCH"] == "main"
        assert result["MAX_LOOP"] == "5"
        assert result["DRY_RUN"] == "true"
        assert "INVALID_KEY" not in result


class TestParseReviewLoopArgs:
    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_basic_args(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config = parse_review_loop_args(["-n", "3", "-t", "main"])
        assert config.max_loop == 3
        assert config.target_branch == "main"
        assert config.current_branch == "feat/x"
        assert config.dry_run is False

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_dry_run(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config = parse_review_loop_args(["-n", "1", "--dry-run"])
        assert config.dry_run is True

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_no_self_review(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config = parse_review_loop_args(["-n", "1", "--no-self-review"])
        assert config.max_subloop == 0

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_missing_max_loop(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        with pytest.raises(SystemExit):
            parse_review_loop_args([])

    @patch("mr_overkill.cli._detect_pr_number", return_value="42")
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_pr_detected(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config = parse_review_loop_args(["-n", "1"])
        assert config.pr_number == "42"

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={
            "TARGET_BRANCH": "main",
            "DRY_RUN": "true",
            "MAX_SUBLOOP": "2",
        },
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_rc_file_defaults(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config = parse_review_loop_args(["-n", "1"])
        assert config.target_branch == "main"
        assert config.dry_run is True
        assert config.max_subloop == 2

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"DRY_RUN": "true"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_cli_overrides_rc(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config = parse_review_loop_args(["-n", "1", "--no-dry-run"])
        assert config.dry_run is False
