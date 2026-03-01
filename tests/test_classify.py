"""Tests for mr_overkill.classify — CLI error classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from mr_overkill.classify import classify_cli_error
from mr_overkill.models import ErrorClass

# ── Transient errors ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stderr_text", "exit_code"),
    [
        pytest.param("Rate limit exceeded", 1, id="rate-limit-text"),
        pytest.param("Too Many Requests", 1, id="too-many-requests"),
        pytest.param("", 429, id="exit-code-429"),
        pytest.param("error code 429 returned", 1, id="body-contains-429"),
        pytest.param("Service overloaded", 1, id="overloaded"),
        pytest.param("error 529", 1, id="error-529"),
        pytest.param("HTTP 500 Internal Server Error", 1, id="500-error"),
        pytest.param("Error 503", 1, id="503-error"),
        pytest.param("internal server error", 0, id="internal-server-error"),
        pytest.param("at capacity", 1, id="capacity"),
        pytest.param("token usage limit reached", 1, id="token-limit"),
        pytest.param("quota has been exceeded", 1, id="quota-exceeded"),
        pytest.param(
            "service temporarily unavailable", 1, id="temporarily-unavailable"
        ),
    ],
)
def test_transient_errors(
    tmp_path: Path, stderr_text: str, exit_code: int
) -> None:
    stderr_file = tmp_path / "stderr.txt"
    stderr_file.write_text(stderr_text)
    assert classify_cli_error(stderr_file, exit_code) == ErrorClass.TRANSIENT


# ── Permanent errors ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stderr_text", "exit_code"),
    [
        pytest.param("authentication failed", 1, id="auth-fail"),
        pytest.param("Unauthorized access", 1, id="unauthorized"),
        pytest.param("HTTP 403", 1, id="403-status"),
        pytest.param("Forbidden resource", 1, id="forbidden"),
        pytest.param("invalid api key provided", 1, id="invalid-api-key"),
        pytest.param("Permission denied", 1, id="permission-denied"),
    ],
)
def test_permanent_errors(
    tmp_path: Path, stderr_text: str, exit_code: int
) -> None:
    stderr_file = tmp_path / "stderr.txt"
    stderr_file.write_text(stderr_text)
    assert classify_cli_error(stderr_file, exit_code) == ErrorClass.PERMANENT


# ── Unknown errors ───────────────────────────────────────────────────


def test_unknown_empty_file(tmp_path: Path) -> None:
    stderr_file = tmp_path / "stderr.txt"
    stderr_file.write_text("")
    assert classify_cli_error(stderr_file, 1) == ErrorClass.UNKNOWN


def test_unknown_generic_error(tmp_path: Path) -> None:
    stderr_file = tmp_path / "stderr.txt"
    stderr_file.write_text("something went wrong")
    assert classify_cli_error(stderr_file, 1) == ErrorClass.UNKNOWN


def test_unknown_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.txt"
    assert classify_cli_error(missing, 1) == ErrorClass.UNKNOWN


# ── Edge cases ───────────────────────────────────────────────────────


def test_reads_at_most_4096_bytes(tmp_path: Path) -> None:
    """Error signature beyond 4096 bytes should not be matched."""
    stderr_file = tmp_path / "stderr.txt"
    # Place the transient keyword beyond the 4096-byte read window.
    stderr_file.write_text("x" * 5000 + "rate limit")
    assert classify_cli_error(stderr_file, 1) == ErrorClass.UNKNOWN


def test_transient_from_exit_code_only(tmp_path: Path) -> None:
    """Exit code alone (e.g. 429) should trigger transient classification."""
    stderr_file = tmp_path / "stderr.txt"
    stderr_file.write_text("no useful info here")
    assert classify_cli_error(stderr_file, 429) == ErrorClass.TRANSIENT
