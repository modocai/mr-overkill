"""Tests for mr_overkill.retry — retry-with-backoff wrappers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mr_overkill.models import BudgetScope
from mr_overkill.retry import (
    extract_result_from_stream,
    retry_claude_cmd,
    retry_codex_cmd,
    wait_for_budget,
)


class TestExtractResultFromStream:
    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "stream.jsonl"
        f.write_text("")
        assert extract_result_from_stream(f) == ""

    def test_missing_file(self, tmp_path: Path) -> None:
        assert extract_result_from_stream(tmp_path / "missing.jsonl") == ""

    def test_extracts_last_result(self, tmp_path: Path) -> None:
        f = tmp_path / "stream.jsonl"
        lines = [
            json.dumps({"type": "result", "result": "first"}),
            json.dumps({"type": "progress", "data": "..."}),
            json.dumps({"type": "result", "result": "final answer"}),
        ]
        f.write_text("\n".join(lines))
        assert extract_result_from_stream(f) == "final answer"

    def test_no_result_events(self, tmp_path: Path) -> None:
        f = tmp_path / "stream.jsonl"
        f.write_text(json.dumps({"type": "progress", "data": "..."}) + "\n")
        assert extract_result_from_stream(f) == ""

    def test_malformed_json_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "stream.jsonl"
        ok_line = json.dumps({"type": "result", "result": "ok"})
        f.write_text(f"not json with result\n{ok_line}")
        assert extract_result_from_stream(f) == "ok"


class TestWaitForBudget:
    def test_budget_ok_immediately(self) -> None:
        check = MagicMock(return_value=True)
        assert wait_for_budget(check, "claude", BudgetScope.MODULE) is True
        check.assert_called_once()

    def test_budget_recovered_after_poll(self) -> None:
        # First call: insufficient, second call: sufficient
        check = MagicMock(side_effect=[False, True])
        sleeps: list[float] = []
        assert (
            wait_for_budget(
                check,
                "claude",
                BudgetScope.MODULE,
                max_wait=7200,
                _sleep_fn=sleeps.append,
            )
            is True
        )
        assert len(sleeps) == 1
        assert sleeps[0] == 600  # BUDGET_POLL_INITIAL

    def test_budget_timeout(self) -> None:
        check = MagicMock(return_value=False)
        sleeps: list[float] = []
        assert (
            wait_for_budget(
                check,
                "codex",
                BudgetScope.MICRO,
                max_wait=500,
                _sleep_fn=sleeps.append,
            )
            is False
        )
        # Should have polled once (500 < 600, so sleep_time = 500)
        assert sleeps[0] == 500

    def test_poll_interval_increases(self) -> None:
        # Budget never recovers
        check = MagicMock(return_value=False)
        sleeps: list[float] = []
        wait_for_budget(
            check,
            "claude",
            BudgetScope.MODULE,
            max_wait=3000,
            _sleep_fn=sleeps.append,
        )
        # First poll: 600, second: min(1200, remaining), etc.
        assert sleeps[0] == 600
        assert sleeps[1] == 1200


class TestRetryClaude:
    @patch("mr_overkill.retry.subprocess.run")
    def test_success_first_try(self, mock_run: MagicMock, tmp_path: Path) -> None:
        output = tmp_path / "output.txt"
        mock_run.return_value = MagicMock(returncode=0)

        result = retry_claude_cmd(output, "test", ["claude", "-p", "-"])
        assert result is True

    @patch("mr_overkill.retry.subprocess.run")
    def test_transient_then_success(self, mock_run: MagicMock, tmp_path: Path) -> None:
        output = tmp_path / "output.txt"
        # First call: transient error (write "rate limit" to output)
        # Second call: success
        call_count = 0

        def side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Write transient error to output file
                stdout = kwargs.get("stdout")
                if stdout and hasattr(stdout, "write"):
                    stdout.write("Rate limit exceeded")
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        sleeps: list[float] = []
        result = retry_claude_cmd(
            output, "test", ["claude", "-p", "-"], _sleep_fn=sleeps.append
        )
        assert result is True
        assert len(sleeps) == 1

    @patch("mr_overkill.retry.subprocess.run")
    def test_permanent_error_gives_up(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        output = tmp_path / "output.txt"

        def side_effect(*args: object, **kwargs: object) -> MagicMock:
            stdout = kwargs.get("stdout")
            if stdout and hasattr(stdout, "write"):
                stdout.write("authentication failed")
            return MagicMock(returncode=1)

        mock_run.side_effect = side_effect
        result = retry_claude_cmd(output, "test", ["claude", "-p", "-"])
        assert result is False

    @patch("mr_overkill.retry.subprocess.run")
    def test_timeout(self, mock_run: MagicMock, tmp_path: Path) -> None:
        output = tmp_path / "output.txt"

        def side_effect(*args: object, **kwargs: object) -> MagicMock:
            stdout = kwargs.get("stdout")
            if stdout and hasattr(stdout, "write"):
                stdout.write("Rate limit exceeded")
            return MagicMock(returncode=1)

        mock_run.side_effect = side_effect
        sleeps: list[float] = []
        result = retry_claude_cmd(
            output,
            "test",
            ["claude", "-p", "-"],
            max_wait=50,
            initial_wait=20,
            _sleep_fn=sleeps.append,
        )
        assert result is False


class TestRetryCodex:
    @patch("mr_overkill.retry.subprocess.run")
    def test_success_first_try(self, mock_run: MagicMock, tmp_path: Path) -> None:
        stderr = tmp_path / "stderr.txt"
        mock_run.return_value = MagicMock(returncode=0)

        result = retry_codex_cmd(stderr, "test", ["codex", "exec"])
        assert result is True

    @patch("mr_overkill.retry.subprocess.run")
    def test_transient_then_success(self, mock_run: MagicMock, tmp_path: Path) -> None:
        stderr = tmp_path / "stderr.txt"
        call_count = 0

        def side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                stderr_f = kwargs.get("stderr")
                if stderr_f and hasattr(stderr_f, "write"):
                    stderr_f.write("Rate limit exceeded")
                return MagicMock(returncode=429)
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        sleeps: list[float] = []
        result = retry_codex_cmd(
            stderr, "test", ["codex", "exec"], _sleep_fn=sleeps.append
        )
        assert result is True
        assert len(sleeps) == 1

    @patch("mr_overkill.retry.subprocess.run")
    def test_permanent_error(self, mock_run: MagicMock, tmp_path: Path) -> None:
        stderr = tmp_path / "stderr.txt"

        def side_effect(*args: object, **kwargs: object) -> MagicMock:
            stderr_f = kwargs.get("stderr")
            if stderr_f and hasattr(stderr_f, "write"):
                stderr_f.write("Unauthorized access")
            return MagicMock(returncode=1)

        mock_run.side_effect = side_effect
        result = retry_codex_cmd(stderr, "test", ["codex", "exec"])
        assert result is False
