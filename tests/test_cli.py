"""Tests for mr_overkill.cli — CLI argument parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill.cli import (
    _load_rc_file,
    _resolve_bool,
    parse_refactor_suggest_args,
    parse_review_loop_args,
)


class TestResolveBool:
    def test_flag_true_wins(self) -> None:
        assert _resolve_bool(True, "false", False) is True

    def test_flag_false_wins(self) -> None:
        assert _resolve_bool(False, "true", True) is False

    def test_rc_value(self) -> None:
        assert _resolve_bool(None, "true", False) is True
        assert _resolve_bool(None, "false", True) is False

    def test_default(self) -> None:
        assert _resolve_bool(None, None, True) is True
        assert _resolve_bool(None, None, False) is False


class TestLoadRcFile:
    @patch("mr_overkill.cli.subprocess.run")
    def test_no_git_root(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _load_rc_file(".overkillrc") == {}

    @patch("mr_overkill.cli.subprocess.run")
    def test_parses_valid_keys(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=str(tmp_path)
        )
        (tmp_path / ".overkill").mkdir()
        rc = tmp_path / ".overkill" / ".overkillrc"
        rc.write_text(
            "# comment\n"
            "TARGET_BRANCH=main\n"
            "MAX_LOOP=5\n"
            "DRY_RUN=true\n"
            'BUDGET_SCOPE="module"\n'
            "REVIEWER_BACKEND=claude\n"
            "INVALID_KEY=ignored\n"
        )
        result = _load_rc_file(".overkillrc")
        assert result["TARGET_BRANCH"] == "main"
        assert result["MAX_LOOP"] == "5"
        assert result["DRY_RUN"] == "true"
        assert result["REVIEWER_BACKEND"] == "claude"
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

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_reviewer_backend_default(
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
        assert config.reviewer_backend == "codex"

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_reviewer_backend_cli(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config = parse_review_loop_args([
            "-n", "1", "--reviewer-backend", "claude",
        ])
        assert config.reviewer_backend == "claude"

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"REVIEWER_BACKEND": "claude"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_reviewer_backend_rc(
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
        assert config.reviewer_backend == "claude"

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"REVIEWER_BACKEND": "claude"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_reviewer_backend_cli_overrides_rc(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config = parse_review_loop_args([
            "-n", "1", "--reviewer-backend", "codex",
        ])
        assert config.reviewer_backend == "codex"

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"REVIEWER_BACKEND": "gpt4"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_reviewer_backend_invalid_rc(
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
            parse_review_loop_args(["-n", "1"])


    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_reviewer_backend_gemini(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config = parse_review_loop_args([
            "-n", "1", "--reviewer-backend", "gemini",
        ])
        assert config.reviewer_backend == "gemini"


class TestParseRefactorSuggestArgs:
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_defaults(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config, extra = parse_refactor_suggest_args([])
        assert config.scope == "auto"
        assert config.target_branch == "develop"
        assert config.max_loop == 1
        assert extra.create_pr is False
        assert extra.with_review is False

    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_scope_and_loops(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config, _extra = parse_refactor_suggest_args([
            "--scope", "module", "-n", "3", "-t", "main",
        ])
        assert config.scope == "module"
        assert config.max_loop == 3
        assert config.target_branch == "main"

    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_with_review_implies_create_pr(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        _config, extra = parse_refactor_suggest_args(["--with-review"])
        assert extra.with_review is True
        assert extra.create_pr is True

    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_with_review_loops(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        _config, extra = parse_refactor_suggest_args([
            "--with-review-loops", "6",
        ])
        assert extra.with_review is True
        assert extra.review_loops == 6

    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_dry_run_and_create_pr(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config, extra = parse_refactor_suggest_args([
            "--dry-run", "--create-pr",
        ])
        assert config.dry_run is True
        assert extra.create_pr is True

    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"SCOPE": "micro", "CREATE_PR": "true"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_rc_file_defaults(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config, extra = parse_refactor_suggest_args([])
        assert config.scope == "micro"
        assert extra.create_pr is True

    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"SCOPE": "micro"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_cli_overrides_rc(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config, _extra = parse_refactor_suggest_args(["--scope", "module"])
        assert config.scope == "module"

    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_reviewer_backend_default(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config, _extra = parse_refactor_suggest_args([])
        assert config.reviewer_backend == "codex"

    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_reviewer_backend_cli(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config, _extra = parse_refactor_suggest_args([
            "--reviewer-backend", "claude",
        ])
        assert config.reviewer_backend == "claude"

    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_reviewer_backend_gemini(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config, _extra = parse_refactor_suggest_args([
            "--reviewer-backend", "gemini",
        ])
        assert config.reviewer_backend == "gemini"
