"""Resume detection for interrupted review/refactor runs.

Ports ``_resume_detect_state`` and ``_resume_reset_working_tree``
from ``common.sh``.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
from pathlib import Path

from mr_overkill.models import ResumeState

_TERMINAL_STATUSES = frozenset({
    "all_clear",
    "no_diff",
    "dry_run",
    "max_iterations_reached",
    "auto_commit_disabled",
})


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, check=False
    )


def detect_state(
    log_dir: Path,
    commit_pattern: str,
    cwd: Path | None = None,
) -> ResumeState:
    """Detect resume state from log directory and git history.

    Logic mirrors ``_resume_detect_state`` in ``common.sh``:
    1. summary.md with terminal status → completed
    2. Highest-numbered review-*.json → last iteration
    3. No review files → no_logs
    4. Git log check for commit of that iteration
    """
    # 1) Check summary.md for completed status
    summary_file = log_dir / "summary.md"
    if summary_file.is_file():
        content = summary_file.read_text(encoding="utf-8")
        m = re.search(r"\*\*Final status\*\*: (\S+)", content)
        final_status = m.group(1) if m else "unknown"
        if final_status in _TERMINAL_STATUSES:
            return ResumeState(
                status="completed",
                resume_from=0,
                reuse_review=False,
                prev_status=final_status,
            )

    # 2) Find highest-numbered review file
    last_i = 0
    for f in log_dir.glob("review-*.json"):
        m = re.match(r"review-(\d+)\.json$", f.name)
        if m:
            n = int(m.group(1))
            if n > last_i:
                last_i = n

    if last_i == 0:
        return ResumeState(
            status="no_logs",
            resume_from=1,
            reuse_review=False,
        )

    # 3) Check if commit exists for this iteration
    log_range = "HEAD"
    start_commit_file = log_dir / "start-commit.txt"
    if start_commit_file.is_file():
        start_commit = start_commit_file.read_text().strip()
        if start_commit:
            verify = _run(["git", "rev-parse", "--verify", start_commit], cwd=cwd)
            if verify.returncode == 0:
                log_range = f"{start_commit}..HEAD"

    # Search for commit with pattern + iteration number
    # Trailing space prevents substring matches (e.g. iteration 1 matching 10)
    grep_str = f"{commit_pattern} {last_i} "
    log_result = _run(
        ["git", "log", "--oneline", "--fixed-strings", f"--grep={grep_str}", log_range],
        cwd=cwd,
    )

    if log_result.returncode == 0 and log_result.stdout.strip():
        # Commit completed → start from next iteration
        next_i = last_i + 1
        max_loop_file = log_dir / "max-loop.txt"
        saved_max = 0
        if max_loop_file.is_file():
            with contextlib.suppress(ValueError):
                saved_max = int(max_loop_file.read_text().strip())

        if saved_max > 0 and next_i > saved_max:
            return ResumeState(
                status="completed",
                resume_from=0,
                reuse_review=False,
                prev_status="max_iterations_reached",
            )
        return ResumeState(
            status="resumable",
            resume_from=next_i,
            reuse_review=False,
        )

    # Commit missing → check if review JSON is valid for reuse
    review_file = log_dir / f"review-{last_i}.json"
    reuse = False
    if review_file.is_file():
        try:
            json.loads(review_file.read_text(encoding="utf-8"))
            reuse = True
        except (json.JSONDecodeError, OSError):
            pass

    return ResumeState(
        status="resumable",
        resume_from=last_i,
        reuse_review=reuse,
    )


def reset_working_tree(cwd: Path | None = None) -> None:
    """Reset working tree to last committed state."""
    _run(["git", "reset", "--quiet", "HEAD"], cwd=cwd)
    toplevel = _run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    root = toplevel.stdout.strip() or "."
    result = _run(["git", "checkout", "--", root], cwd=cwd)
    if result.returncode != 0:
        import logging

        logging.getLogger(__name__).warning(
            "git checkout failed during resume reset: %s", result.stderr.strip()
        )
