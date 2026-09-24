"""Tests for gemini_trust — forwarding the user's trust decision into the sandbox."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill import gemini_trust
from mr_overkill.retry import retry_gemini_cmd

TRUST_VAR = gemini_trust.TRUST_ENV_VAR


def _trust_file(tmp_path: Path, rules: dict[str, str]) -> dict[str, str]:
    """Write *rules* as a trust list and return an env that points at it."""
    path = tmp_path / "trustedFolders.json"
    path.write_text(json.dumps(rules))
    return {"GEMINI_CLI_TRUSTED_FOLDERS_PATH": str(path)}


class TestIsPathTrusted:
    def test_trusted_folder_covers_its_subdirectories(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "sub").mkdir(parents=True)
        rules = {str(repo): "TRUST_FOLDER"}

        assert gemini_trust.is_path_trusted(rules, str(repo)) is True
        assert gemini_trust.is_path_trusted(rules, str(repo / "sub")) is True

    def test_sibling_with_a_shared_prefix_is_not_covered(self, tmp_path: Path) -> None:
        # "/x/repo" must not trust "/x/repo-evil".
        (tmp_path / "repo").mkdir()
        (tmp_path / "repo-evil").mkdir()
        rules = {str(tmp_path / "repo"): "TRUST_FOLDER"}

        assert gemini_trust.is_path_trusted(rules, str(tmp_path / "repo-evil")) is None

    def test_trust_parent_trusts_the_rule_s_parent(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        rules = {str(tmp_path / "a"): "TRUST_PARENT"}

        assert gemini_trust.is_path_trusted(rules, str(tmp_path / "b")) is True

    def test_longest_rule_wins(self, tmp_path: Path) -> None:
        # A distrusted repo inside a trusted home stays distrusted.
        repo = tmp_path / "repo"
        repo.mkdir()
        rules = {str(tmp_path): "TRUST_FOLDER", str(repo): "DO_NOT_TRUST"}

        assert gemini_trust.is_path_trusted(rules, str(repo)) is False
        assert gemini_trust.is_path_trusted(rules, str(tmp_path)) is True

    def test_no_matching_rule(self, tmp_path: Path) -> None:
        assert (
            gemini_trust.is_path_trusted({"/elsewhere": "TRUST_FOLDER"}, str(tmp_path))
            is None
        )

    def test_symlinked_location_resolves(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)

        assert (
            gemini_trust.is_path_trusted({str(real): "TRUST_FOLDER"}, str(link)) is True
        )

    @pytest.mark.skipif(
        sys.platform != "darwin", reason="case folding is macOS/Windows only"
    )
    def test_case_is_folded_on_macos(self, tmp_path: Path) -> None:
        # Gemini itself writes lower-cased entries on macOS.
        assert (
            gemini_trust.is_path_trusted(
                {str(tmp_path).lower(): "TRUST_FOLDER"}, str(tmp_path)
            )
            is True
        )


class TestSandboxEnv:
    def test_trusted_folder_gets_the_variable(self, tmp_path: Path) -> None:
        env = _trust_file(tmp_path, {str(tmp_path): "TRUST_FOLDER"})

        result = gemini_trust.sandbox_env(tmp_path, env)

        assert result is not None
        assert result[TRUST_VAR] == "true"
        # The rest of the environment is carried over, not replaced.
        assert (
            result["GEMINI_CLI_TRUSTED_FOLDERS_PATH"]
            == env["GEMINI_CLI_TRUSTED_FOLDERS_PATH"]
        )

    @pytest.mark.parametrize("level", ["DO_NOT_TRUST", None])
    def test_untrusted_folder_is_left_alone(
        self, tmp_path: Path, level: str | None
    ) -> None:
        rules = {str(tmp_path): level} if level else {"/elsewhere": "TRUST_FOLDER"}
        env = _trust_file(tmp_path, rules)

        assert gemini_trust.sandbox_env(tmp_path, env) is None

    @pytest.mark.parametrize("value", ["false", "true"])
    def test_user_s_explicit_setting_wins(self, tmp_path: Path, value: str) -> None:
        env = {
            **_trust_file(tmp_path, {str(tmp_path): "TRUST_FOLDER"}),
            TRUST_VAR: value,
        }

        assert gemini_trust.sandbox_env(tmp_path, env) is None

    def test_restricted_mode_is_respected(self, tmp_path: Path) -> None:
        env = {
            **_trust_file(tmp_path, {str(tmp_path): "TRUST_FOLDER"}),
            "GEMINI_RESTRICTED_MODE": "true",
        }

        assert gemini_trust.sandbox_env(tmp_path, env) is None

    def test_missing_trust_list(self, tmp_path: Path) -> None:
        env = {"GEMINI_CLI_TRUSTED_FOLDERS_PATH": str(tmp_path / "absent.json")}

        assert gemini_trust.sandbox_env(tmp_path, env) is None

    def test_trust_list_with_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "trustedFolders.json"
        path.write_text(
            "{\n  // a comment Gemini accepts\n"
            f'  {json.dumps(str(tmp_path))}: /* inline */ "TRUST_FOLDER",\n'
            '  "//server/share": "DO_NOT_TRUST"\n}'
        )

        env = {"GEMINI_CLI_TRUSTED_FOLDERS_PATH": str(path)}

        result = gemini_trust.sandbox_env(tmp_path, env)
        assert result is not None
        assert result[gemini_trust.TRUST_ENV_VAR] == "true"

    def test_comment_markers_inside_strings_are_kept(self) -> None:
        text = '{"//a/*b": "x\\"//", "c": 1} // tail'

        assert json.loads(gemini_trust._strip_json_comments(text)) == {
            "//a/*b": 'x"//',
            "c": 1,
        }

    def test_unparseable_trust_list_grants_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "trustedFolders.json"
        path.write_text(f"{{{json.dumps(str(tmp_path))}: TRUST_FOLDER}}")

        env = {"GEMINI_CLI_TRUSTED_FOLDERS_PATH": str(path)}

        assert gemini_trust.sandbox_env(tmp_path, env) is None

    def test_default_location_follows_gemini_cli_home(self, tmp_path: Path) -> None:
        (tmp_path / ".gemini").mkdir()
        (tmp_path / ".gemini" / "trustedFolders.json").write_text(
            json.dumps({str(tmp_path): "TRUST_FOLDER"})
        )

        result = gemini_trust.sandbox_env(tmp_path, {"GEMINI_CLI_HOME": str(tmp_path)})

        assert result is not None and result[TRUST_VAR] == "true"


class TestRunner:
    """The retry runner is where the environment actually reaches Gemini."""

    @patch("mr_overkill.retry.subprocess.run")
    def test_gemini_runs_with_the_forwarded_env(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        forwarded = {TRUST_VAR: "true"}
        with patch.object(gemini_trust, "sandbox_env", return_value=forwarded):
            retry_gemini_cmd(
                tmp_path / "out.txt", "t", ["gemini", "--sandbox", "-p", "-"]
            )

        assert mock_run.call_args.kwargs["env"] is forwarded

    @patch("mr_overkill.retry.subprocess.run")
    def test_agy_does_not_consult_the_gemini_trust_list(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        sandbox_env = MagicMock(return_value={TRUST_VAR: "true"})
        with patch.object(gemini_trust, "sandbox_env", sandbox_env):
            retry_gemini_cmd(tmp_path / "out.txt", "t", ["agy", "--sandbox", "-p", "x"])

        sandbox_env.assert_not_called()
        assert mock_run.call_args.kwargs["env"] is None

    @patch("mr_overkill.retry.subprocess.run")
    def test_exit_55_explains_itself(
        self, mock_run: MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_run.return_value = MagicMock(returncode=gemini_trust.UNTRUSTED_EXIT_CODE)
        with (
            patch.object(gemini_trust, "sandbox_env", return_value=None),
            caplog.at_level(logging.ERROR, logger="mr_overkill.retry"),
        ):
            ok = retry_gemini_cmd(
                tmp_path / "out.txt",
                "gemini review",
                ["gemini", "--sandbox", "-p", "-"],
            )

        assert ok is False
        assert mock_run.call_count == 1  # not retried
        assert TRUST_VAR in caplog.text
        assert "Non-transient error" not in caplog.text
