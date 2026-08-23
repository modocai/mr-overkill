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
from mr_overkill.models import LoopConfig


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

    @patch("mr_overkill.cli.subprocess.run")
    def test_parses_no_budget_gate(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=str(tmp_path)
        )
        (tmp_path / ".overkill").mkdir()
        rc = tmp_path / ".overkill" / ".overkillrc"
        rc.write_text("NO_BUDGET_GATE=True\n")
        assert _load_rc_file(".overkillrc")["NO_BUDGET_GATE"] == "true"

    @patch("mr_overkill.cli.subprocess.run")
    def test_rejects_non_boolean_no_budget_gate(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=str(tmp_path)
        )
        (tmp_path / ".overkill").mkdir()
        rc = tmp_path / ".overkill" / ".overkillrc"
        rc.write_text("NO_BUDGET_GATE=yes\n")
        with pytest.raises(SystemExit):
            _load_rc_file(".overkillrc")


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
    def test_budget_gate_enabled_by_default(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/repo")
        config = parse_review_loop_args(["-n", "1"])
        assert config.skip_budget_gate is False

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_no_budget_gate_flag(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/repo")
        config = parse_review_loop_args(["-n", "1", "--no-budget-gate"])
        assert config.skip_budget_gate is True

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"NO_BUDGET_GATE": "true"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_no_budget_gate_from_rc(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/repo")
        config = parse_review_loop_args(["-n", "1"])
        assert config.skip_budget_gate is True

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

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_ci_trigger_mode_default(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/repo")
        config = parse_review_loop_args(["-n", "1"])
        assert config.ci_trigger_mode == "last-only"

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_ci_trigger_mode_cli(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/repo")
        config = parse_review_loop_args([
            "-n", "1", "--ci-trigger-mode", "last-only",
        ])
        assert config.ci_trigger_mode == "last-only"

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"CI_TRIGGER_MODE": "none"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_ci_trigger_mode_rc(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/repo")
        config = parse_review_loop_args(["-n", "1"])
        assert config.ci_trigger_mode == "none"

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"CI_TRIGGER_MODE": "none"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_ci_trigger_mode_cli_overrides_rc(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/repo")
        config = parse_review_loop_args([
            "-n", "1", "--ci-trigger-mode", "every",
        ])
        assert config.ci_trigger_mode == "every"

    @patch("mr_overkill.cli._detect_pr_number", return_value=None)
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"CI_TRIGGER_MODE": "bogus"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_ci_trigger_mode_invalid_rc(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/repo")
        with pytest.raises(SystemExit):
            parse_review_loop_args(["-n", "1"])

    def test_ci_trigger_mode_invalid_cli(self) -> None:
        with pytest.raises(SystemExit):
            parse_review_loop_args([
                "-n", "1", "--ci-trigger-mode", "bogus",
            ])


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
        assert config.skip_budget_gate is False
        # refactor-suggest does not push a trigger commit, so iteration
        # commits must trigger CI directly — never inherit "last-only".
        assert config.ci_trigger_mode == "every"

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
    def test_no_budget_gate_flag(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/repo"
        )
        config, _extra = parse_refactor_suggest_args(["--no-budget-gate"])
        assert config.skip_budget_gate is True

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


class TestCommitScopeArgs:
    """`--commit` / `--push` wiring for commit-scope review."""

    SHA = "a" * 40

    def _parse(self, argv: list[str], rc: dict[str, str] | None = None,
               resolve: object = None) -> LoopConfig:
        head = "b" * 40
        default = {"HEAD": head}
        table = {**default, **({} if resolve is None else resolve)}  # type: ignore[dict-item]
        with (
            patch("mr_overkill.cli._detect_pr_number", return_value="42"),
            patch("mr_overkill.cli._detect_current_branch", return_value="main"),
            patch("mr_overkill.cli._load_rc_file", return_value=rc or {}),
            patch(
                "mr_overkill.cli.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="/tmp/repo"),
            ),
            patch(
                "mr_overkill.cli.resolve_commit",
                side_effect=lambda rev: table.get(rev, self.SHA if rev else None),
            ),
        ):
            return parse_review_loop_args(argv)

    def test_sets_scope_fields(self) -> None:
        config = self._parse(["-n", "2", "--commit", "abc123"])
        assert config.scope_commit == self.SHA
        assert config.scope_diff_file == config.log_dir / "scope.diff"

    def test_target_becomes_head_sha(self) -> None:
        config = self._parse(["-n", "2", "--commit", "abc123"])
        assert config.target_branch == "b" * 40

    def test_suppresses_pr_number(self) -> None:
        """A review/* branch has no PR; commenting on the branch we happened
        to start from would post findings onto an unrelated PR."""
        config = self._parse(["-n", "2", "--commit", "abc123"])
        assert config.pr_number is None

    def test_forces_ci_trigger_every(self) -> None:
        config = self._parse(["-n", "2", "--commit", "abc123"])
        assert config.ci_trigger_mode == "every"

    def test_branch_stays_local_by_default(self) -> None:
        config = self._parse(["-n", "2", "--commit", "abc123"])
        assert config.push_branch is False

    def test_push_flag_opts_in(self) -> None:
        config = self._parse(["-n", "2", "--commit", "abc123", "--push"])
        assert config.push_branch is True

    def test_push_from_rc(self) -> None:
        config = self._parse(
            ["-n", "2", "--commit", "abc123"], rc={"COMMIT_SCOPE_PUSH": "true"}
        )
        assert config.push_branch is True

    def test_rejects_range_syntax(self) -> None:
        with pytest.raises(SystemExit):
            self._parse(["-n", "2", "--commit", "aaa..bbb"])

    def test_rejects_target_combination(self) -> None:
        with pytest.raises(SystemExit):
            self._parse(["-n", "2", "--commit", "abc123", "-t", "main"])

    def test_rejects_unresolvable_rev(self) -> None:
        with pytest.raises(SystemExit):
            self._parse(["-n", "2", "--commit", "nope"], resolve={"nope": None})

    @patch("mr_overkill.cli._detect_pr_number", return_value="42")
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch("mr_overkill.cli._load_rc_file", return_value={})
    @patch("mr_overkill.cli.subprocess.run")
    def test_normal_mode_leaves_scope_unset(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/repo")
        config = parse_review_loop_args(["-n", "1"])
        assert config.scope_commit is None
        assert config.scope_diff_file is None
        assert config.push_branch is True
        assert config.pr_number == "42"

    @patch("mr_overkill.cli._detect_pr_number", return_value="42")
    @patch("mr_overkill.cli._detect_current_branch", return_value="feat/x")
    @patch(
        "mr_overkill.cli._load_rc_file",
        return_value={"COMMIT_SCOPE_PUSH": "false"},
    )
    @patch("mr_overkill.cli.subprocess.run")
    def test_normal_mode_ignores_commit_scope_push(
        self,
        mock_run: MagicMock,
        mock_rc: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        """The rc key describes the auto-created review/* branch. Honouring it
        here would leave the first fix commit unpushed on a fresh branch."""
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/repo")
        config = parse_review_loop_args(["-n", "1"])
        assert config.push_branch is True


class TestCommitScopeResume:
    """`scope-commit.txt` keeps a resumed run pointed at the same commit."""

    SHA = "c" * 40

    def _parse(self, argv: list[str], log_dir: Path) -> LoopConfig:
        with (
            patch("mr_overkill.cli._detect_pr_number", return_value=None),
            patch(
                "mr_overkill.cli._detect_current_branch",
                return_value="review/ccccccc-20260101-000000",
            ),
            patch("mr_overkill.cli._load_rc_file", return_value={}),
            patch(
                "mr_overkill.cli.subprocess.run",
                return_value=MagicMock(returncode=0, stdout=str(log_dir)),
            ),
            patch("mr_overkill.cli.resolve_commit", side_effect=lambda rev: self.SHA),
        ):
            return parse_review_loop_args(argv)

    def _log_dir(self, tmp_path: Path) -> Path:
        log_dir = tmp_path / ".overkill" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "max-loop.txt").write_text("3")
        (log_dir / "target-branch.txt").write_text("d" * 40)
        return log_dir

    def test_restores_scope_commit(self, tmp_path: Path) -> None:
        log_dir = self._log_dir(tmp_path)
        (log_dir / "scope-commit.txt").write_text(self.SHA)
        config = self._parse(["--resume"], tmp_path)
        assert config.scope_commit == self.SHA
        assert config.scope_diff_file == log_dir / "scope.diff"

    def test_rejects_mismatched_commit(self, tmp_path: Path) -> None:
        log_dir = self._log_dir(tmp_path)
        (log_dir / "scope-commit.txt").write_text("f" * 40)
        with pytest.raises(SystemExit):
            self._parse(["--resume", "--commit", "abc123"], tmp_path)

    def test_keeps_the_restored_target(self, tmp_path: Path) -> None:
        """HEAD has moved past the work branch's base by the fix commits made
        so far, so re-reading it would trip the loop's resume target check."""
        log_dir = self._log_dir(tmp_path)
        (log_dir / "scope-commit.txt").write_text(self.SHA)
        config = self._parse(["--resume"], tmp_path)
        assert config.target_branch == "d" * 40

    def test_restores_the_push_policy(self, tmp_path: Path) -> None:
        """A run started with --push keeps publishing its fix commits after
        an interruption, even though the flag is not repeated on resume."""
        log_dir = self._log_dir(tmp_path)
        (log_dir / "scope-commit.txt").write_text(self.SHA)
        (log_dir / "push-branch.txt").write_text("true")
        config = self._parse(["--resume"], tmp_path)
        assert config.push_branch is True

    def test_rejects_an_explicit_target(self, tmp_path: Path) -> None:
        """The base was fixed when the work branch was created, so an explicit
        -t can only disagree with it — say so instead of silently ignoring it."""
        log_dir = self._log_dir(tmp_path)
        (log_dir / "scope-commit.txt").write_text(self.SHA)
        with pytest.raises(SystemExit):
            self._parse(["--resume", "-t", "develop"], tmp_path)
