"""Retry-with-backoff wrappers for Claude/Codex CLI calls.

Ports retry logic from ``bin/lib/retry.sh`` to Python.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from mr_overkill.classify import classify_cli_error
from mr_overkill.models import BudgetCheckFn, BudgetScope, ErrorClass

SleepFn = Callable[[float], object]

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_WAIT = 7200
DEFAULT_INITIAL_WAIT = 30
MAX_SINGLE_SLEEP = 300
BUDGET_POLL_INITIAL = 600
BUDGET_POLL_MAX = 1200


def extract_result_from_stream(stream_path: Path) -> str:
    """Extract final result text from a Claude stream-json event log.

    Returns empty string if the file is empty or no result event found.
    """
    if not stream_path.is_file() or stream_path.stat().st_size == 0:
        return ""

    last_result = ""
    for line in stream_path.read_text(encoding="utf-8").splitlines():
        if '"type"' not in line or '"result"' not in line:
            continue
        try:
            data = json.loads(line)
            if data.get("result"):
                last_result = data["result"]
        except (json.JSONDecodeError, KeyError):
            continue
    return last_result


def wait_for_budget(
    budget_check_fn: BudgetCheckFn,
    tool: str,
    scope: BudgetScope,
    max_wait: int = DEFAULT_MAX_WAIT,
    *,
    _sleep_fn: SleepFn = time.sleep,
) -> bool:
    """Wait until the tool's budget is sufficient for a CLI call.

    Returns True if budget is OK, False on timeout.
    """

    if budget_check_fn(tool, scope, 0):
        return True

    logger.info(
        "Budget insufficient for %s (scope: %s). Waiting for reset...",
        tool,
        scope,
    )

    elapsed = 0
    poll_wait = BUDGET_POLL_INITIAL

    while elapsed < max_wait:
        sleep_time = min(poll_wait, max_wait - elapsed)
        logger.info(
            "Polling budget in %ds (%d/%ds elapsed)...",
            sleep_time,
            elapsed,
            max_wait,
        )
        _sleep_fn(sleep_time)
        elapsed += sleep_time

        if budget_check_fn(tool, scope, 0):
            logger.info("Budget restored for %s.", tool)
            return True

        if poll_wait < BUDGET_POLL_MAX:
            poll_wait = min(poll_wait * 2, BUDGET_POLL_MAX)

    logger.warning("Budget wait timeout (%ds) for %s.", max_wait, tool)
    return False


def retry_claude_cmd(
    output_path: Path,
    label: str,
    cmd_args: list[str],
    *,
    stdin: str | None = None,
    max_wait: int = DEFAULT_MAX_WAIT,
    initial_wait: int = DEFAULT_INITIAL_WAIT,
    diagnostic_log: bool = False,
    _sleep_fn: SleepFn = time.sleep,
) -> bool:
    """Retry a Claude CLI command with exponential backoff.

    Pipes *stdin* to the command and writes output to *output_path*.
    When *diagnostic_log* is True, appends ``--output-format stream-json``
    and extracts the plain-text result from the event stream.

    Returns True on success, False on permanent/unknown error or timeout.
    """
    wait = initial_wait
    elapsed = 0
    attempt = 1

    stream_file = output_path.with_suffix(".stream.jsonl") if diagnostic_log else None

    while True:
        rc = _run_claude_once(
            cmd_args, stdin, output_path, stream_file, diagnostic_log, label
        )

        if rc == 0:
            return True

        # Classify error
        error_file = (
            output_path.with_suffix(".stderr")
            if diagnostic_log
            else output_path
        )
        error_class = classify_cli_error(error_file, rc)

        if error_class != ErrorClass.TRANSIENT:
            logger.warning(
                "[%s] Non-transient error (%s, exit=%d). Giving up.",
                label,
                error_class,
                rc,
            )
            if diagnostic_log and error_file.is_file():
                shutil.copy2(error_file, output_path)
            return False

        if elapsed >= max_wait:
            logger.warning(
                "[%s] Retry timeout (%d/%ds). Giving up.",
                label,
                elapsed,
                max_wait,
            )
            if diagnostic_log and error_file.is_file():
                shutil.copy2(error_file, output_path)
            return False

        sleep_time = min(wait, MAX_SINGLE_SLEEP)
        if elapsed + sleep_time > max_wait:
            sleep_time = max_wait - elapsed

        attempt += 1
        logger.info(
            "[%s] Transient error (exit=%d). Retry #%d in %ds...",
            label,
            rc,
            attempt,
            sleep_time,
        )
        _sleep_fn(sleep_time)
        elapsed += sleep_time
        wait *= 2


def _run_claude_once(
    cmd_args: list[str],
    stdin: str | None,
    output_path: Path,
    stream_file: Path | None,
    diagnostic_log: bool,
    label: str,
) -> int:
    """Execute a single Claude CLI invocation. Returns the exit code."""
    try:
        if diagnostic_log and stream_file is not None:
            stderr_path = output_path.with_suffix(".stderr")
            with (
                stream_file.open("w", encoding="utf-8") as sf,
                stderr_path.open("w", encoding="utf-8") as ef,
            ):
                result = subprocess.run(
                    [*cmd_args, "--output-format", "stream-json"],
                    input=stdin,
                    stdout=sf,
                    stderr=ef,
                    text=True,
                    check=False,
                )
            if result.returncode == 0:
                extracted = extract_result_from_stream(stream_file)
                output_path.write_text(extracted, encoding="utf-8")
                if not extracted:
                    logger.warning(
                        "[%s] stream-json result extraction produced empty output.",
                        label,
                    )
        else:
            with output_path.open("w", encoding="utf-8") as of:
                result = subprocess.run(
                    cmd_args,
                    input=stdin,
                    stdout=of,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
    except FileNotFoundError:
        logger.error("[%s] claude CLI not found on PATH.", label)
        return 1

    return result.returncode


def retry_gemini_cmd(
    output_path: Path,
    label: str,
    cmd_args: list[str],
    *,
    stdin: str | None = None,
    max_wait: int = DEFAULT_MAX_WAIT,
    initial_wait: int = DEFAULT_INITIAL_WAIT,
    _sleep_fn: SleepFn = time.sleep,
) -> bool:
    """Retry a Gemini CLI command with exponential backoff.

    Pipes *stdin* to the command and writes output to *output_path*.

    Returns True on success, False on permanent/unknown error or timeout.
    """
    wait = initial_wait
    elapsed = 0
    attempt = 1

    while True:
        rc = _run_gemini_once(cmd_args, stdin, output_path, label)

        if rc == 0:
            return True

        error_class = classify_cli_error(output_path, rc)

        if error_class != ErrorClass.TRANSIENT:
            logger.warning(
                "[%s] Non-transient error (%s, exit=%d). Giving up.",
                label,
                error_class,
                rc,
            )
            return False

        if elapsed >= max_wait:
            logger.warning(
                "[%s] Retry timeout (%d/%ds). Giving up.",
                label,
                elapsed,
                max_wait,
            )
            return False

        sleep_time = min(wait, MAX_SINGLE_SLEEP)
        if elapsed + sleep_time > max_wait:
            sleep_time = max_wait - elapsed

        attempt += 1
        logger.info(
            "[%s] Transient error (exit=%d). Retry #%d in %ds...",
            label,
            rc,
            attempt,
            sleep_time,
        )
        _sleep_fn(sleep_time)
        elapsed += sleep_time
        wait *= 2


def _run_gemini_once(
    cmd_args: list[str],
    stdin: str | None,
    output_path: Path,
    label: str,
) -> int:
    """Execute a single Gemini CLI invocation. Returns the exit code."""
    try:
        with output_path.open("w", encoding="utf-8") as of:
            result = subprocess.run(
                cmd_args,
                input=stdin,
                stdout=of,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
    except FileNotFoundError:
        logger.error("[%s] gemini CLI not found on PATH.", label)
        return 1

    return result.returncode


def retry_codex_cmd(
    stderr_path: Path,
    label: str,
    cmd_args: list[str],
    *,
    max_wait: int = DEFAULT_MAX_WAIT,
    initial_wait: int = DEFAULT_INITIAL_WAIT,
    _sleep_fn: SleepFn = time.sleep,
) -> bool:
    """Retry a Codex CLI command with exponential backoff.

    Codex writes its primary output via the ``-o <file>`` flag, so stdout
    is intentionally not captured here.  Only stderr is saved for error
    classification and retry decisions.

    Returns True on success, False on permanent/unknown error or timeout.
    """
    wait = initial_wait
    elapsed = 0
    attempt = 1

    while True:
        with stderr_path.open("w", encoding="utf-8") as ef:
            try:
                result = subprocess.run(
                    cmd_args,
                    stdin=subprocess.DEVNULL,
                    stderr=ef,
                    text=True,
                    check=False,
                )
            except FileNotFoundError:
                logger.error(
                    "[%s] Command not found: %s", label, cmd_args[0]
                )
                return False

        if result.returncode == 0:
            return True

        error_class = classify_cli_error(stderr_path, result.returncode)

        if error_class != ErrorClass.TRANSIENT:
            logger.warning(
                "[%s] Non-transient error (%s, exit=%d). Giving up.",
                label,
                error_class,
                result.returncode,
            )
            return False

        if elapsed >= max_wait:
            logger.warning(
                "[%s] Retry timeout (%d/%ds). Giving up.",
                label,
                elapsed,
                max_wait,
            )
            return False

        sleep_time = min(wait, MAX_SINGLE_SLEEP)
        if elapsed + sleep_time > max_wait:
            sleep_time = max_wait - elapsed

        attempt += 1
        logger.info(
            "[%s] Transient error (exit=%d). Retry #%d in %ds...",
            label,
            result.returncode,
            attempt,
            sleep_time,
        )
        _sleep_fn(sleep_time)
        elapsed += sleep_time
        wait *= 2
