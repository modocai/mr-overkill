"""Reviewer CLI calls are bounded in time, and Gemini runs without web tools."""

from __future__ import annotations

import logging
import os
import sys
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill import retry
from mr_overkill.agents import ParallelReviewAgent
from mr_overkill.cli import parse_refactor_suggest_args, parse_review_loop_args
from mr_overkill.loop_engine import review_fix_loop
from mr_overkill.models import FinalStatus, LoopConfig
from mr_overkill.retry import (
    CommandTimeoutError,
    call_timeout,
    retry_claude_cmd,
    retry_codex_cmd,
    retry_gemini_cmd,
)
from mr_overkill.two_step_fix import GEMINI_POLICY, backend_command

HANG = [sys.executable, "-c", "import time; time.sleep(60)"]


class TestRunner:
    def test_hung_process_is_killed_and_not_retried(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sleep_fn = MagicMock()
        start = time.monotonic()
        with (
            call_timeout(1),
            caplog.at_level(logging.ERROR, logger="mr_overkill.retry"),
        ):
            ok = retry_codex_cmd(
                tmp_path / "cli.stderr",
                "gemini review",
                HANG,
                _sleep_fn=sleep_fn,
            )

        assert ok is False
        assert time.monotonic() - start < 10
        sleep_fn.assert_not_called()
        assert "[gemini review] Timed out after 1s" in caplog.text
        assert "--reviewer-timeout" in caplog.text

    @pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
    def test_grandchildren_are_killed_too(self, tmp_path: Path) -> None:
        # Sandboxed CLIs run the real work in a child process.
        pid_file = tmp_path / "grandchild.pid"
        code = (
            "import subprocess, sys, time;"
            f"p = subprocess.Popen({HANG!r});"
            f"open({str(pid_file)!r}, 'w').write(str(p.pid));"
            "time.sleep(60)"
        )
        with call_timeout(1):
            assert not retry_codex_cmd(
                tmp_path / "cli.stderr",
                "t",
                [sys.executable, "-c", code],
            )

        grandchild = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        pytest.fail("grandchild survived the timeout")

    def test_fast_process_is_unaffected(self, tmp_path: Path) -> None:
        code = "import sys; print(sys.stdin.read(), file=sys.stderr)"
        with call_timeout(30):
            assert retry_codex_cmd(
                tmp_path / "cli.stderr",
                "t",
                [sys.executable, "-c", code],
                stdin="hello",
            )
        assert (tmp_path / "cli.stderr").read_text().strip() == "hello"

    def test_no_timeout_keeps_the_plain_run_path(self, tmp_path: Path) -> None:
        with patch("mr_overkill.retry.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with call_timeout(0):
                assert retry_codex_cmd(tmp_path / "cli.stderr", "t", ["codex"])
        mock_run.assert_called_once()

    @pytest.mark.parametrize("runner", ["claude", "gemini"])
    def test_every_retry_wrapper_gives_up_on_timeout(
        self, tmp_path: Path, runner: str
    ) -> None:
        with patch(
            "mr_overkill.retry._run_command",
            side_effect=CommandTimeoutError(5),
        ) as mock_run:
            if runner == "claude":
                ok = retry_claude_cmd(tmp_path / "out.txt", "t", ["claude", "-p", "-"])
            else:
                ok = retry_gemini_cmd(tmp_path / "out.txt", "t", ["gemini", "-p", "-"])

        assert ok is False
        assert mock_run.call_count == 1


@contextmanager
def _recording_timeout() -> Iterator[list[int | None]]:
    """Record the timeout each reviewer sees when it starts a CLI call."""
    seen: list[int | None] = []

    def reviewer(output_path: Path, iteration: int) -> bool:
        seen.append(retry._call_timeout.get())
        return False

    with patch("mr_overkill.agents.create_review_agent", return_value=reviewer):
        yield seen


class TestWiring:
    def test_parallel_reviewer_threads_see_the_timeout(self, tmp_path: Path) -> None:
        config = LoopConfig(
            "feat/x",
            "develop",
            1,
            log_dir=tmp_path,
            reviewer_backend="gemini,claude",
            reviewer_timeout=42,
        )
        with _recording_timeout() as seen:
            assert ParallelReviewAgent(config)(tmp_path / "review-1.json", 1) is False

        assert seen == [42, 42]

    def test_loop_bounds_the_review_but_not_the_fixer(self, tmp_path: Path) -> None:
        config = LoopConfig(
            "feat/x",
            "develop",
            1,
            log_dir=tmp_path,
            reviewer_timeout=42,
        )
        seen: list[int | None] = []

        def reviewer(output_path: Path, iteration: int) -> bool:
            seen.append(retry._call_timeout.get())
            return False

        with (
            patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[]),
            patch("mr_overkill.loop_engine._validate_target_branch", return_value=True),
            patch("mr_overkill.loop_engine._no_diff", return_value=False),
            patch("mr_overkill.loop_engine._save_metadata"),
        ):
            result = review_fix_loop(
                config,
                reviewer=reviewer,
                fixer=MagicMock(),
                cwd=tmp_path,
            )

        assert result.final_status == FinalStatus.CODEX_ERROR
        assert seen == [42]
        assert retry._call_timeout.get() is None


_CLI_PATCHES = (
    patch("mr_overkill.cli._detect_pr_number", return_value=None),
    patch("mr_overkill.cli._detect_current_branch", return_value="feat/x"),
    patch(
        "mr_overkill.cli.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="/tmp/repo"),
    ),
)


@contextmanager
def _cli(rc: dict[str, str]) -> Iterator[None]:
    with (
        _CLI_PATCHES[0],
        _CLI_PATCHES[1],
        _CLI_PATCHES[2],
        patch("mr_overkill.cli._load_rc_file", return_value=rc),
    ):
        yield


class TestCli:
    @pytest.mark.parametrize(
        ("argv", "rc", "expected"),
        [
            ([], {}, 1200),
            ([], {"REVIEWER_TIMEOUT": "600"}, 600),
            (["--reviewer-timeout", "0"], {"REVIEWER_TIMEOUT": "600"}, 0),
        ],
    )
    def test_review_loop(
        self, argv: list[str], rc: dict[str, str], expected: int
    ) -> None:
        with _cli(rc):
            config = parse_review_loop_args(["-n", "1", *argv])
        assert config.reviewer_timeout == expected

    def test_refactor_suggest(self) -> None:
        with _cli({}):
            config, _ = parse_refactor_suggest_args(["--reviewer-timeout", "90"])
        assert config.reviewer_timeout == 90

    def test_negative_is_rejected(self) -> None:
        with _cli({}), pytest.raises(SystemExit):
            parse_review_loop_args(["-n", "1", "--reviewer-timeout", "-1"])


class TestGeminiPolicy:
    @pytest.mark.parametrize("edit", [False, True])
    def test_every_gemini_call_loads_the_policy(self, edit: bool) -> None:
        cmd = backend_command("gemini", edit=edit)
        assert cmd[cmd.index("--policy") + 1] == GEMINI_POLICY
        assert cmd[-2:] == ["-p", "-"]

    def test_policy_denies_web_tools(self) -> None:
        rules = tomllib.loads(Path(GEMINI_POLICY).read_text())["rule"]
        denied = {
            name
            for rule in rules
            if rule["decision"] == "deny"
            for name in rule["toolName"]
        }
        assert {"google_web_search", "web_fetch"} <= denied
