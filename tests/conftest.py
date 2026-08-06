"""Shared pytest fixtures for overkill tests."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from mr_overkill.models import LoopConfig


@pytest.fixture(autouse=True)
def _isolate_budget_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep budget detection independent of the developer's own environment.

    ``OPENAI_API_KEY``/``CODEX_HOME`` change which auth mode is detected and
    ``OVERKILL_SKIP_BUDGET`` bypasses the gate entirely, so tests would pass
    or fail depending on the shell they run in.
    """
    for var in ("OPENAI_API_KEY", "CODEX_HOME", "OVERKILL_SKIP_BUDGET"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository in a temporary directory.

    Returns the repo root path.  The repo has one initial commit so that
    ``git diff HEAD`` and similar commands work.
    """
    subprocess.run(
        ["git", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    # Initial commit so HEAD exists
    readme = tmp_path / "README.md"
    readme.write_text("# test\n")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


@pytest.fixture()
def make_loop_config(
    tmp_path: Path,
) -> Callable[..., LoopConfig]:
    """Factory fixture that creates a LoopConfig with sensible test defaults.

    Usage::

        def test_something(make_loop_config):
            config = make_loop_config(max_loop=3)
    """

    def _factory(**overrides: object) -> LoopConfig:
        log_dir = tmp_path / "logs"
        log_dir.mkdir(exist_ok=True)
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        defaults: dict[str, object] = {
            "current_branch": "feat/test",
            "target_branch": "develop",
            "max_loop": 1,
            "max_subloop": 0,
            "log_dir": log_dir,
            "prompts_dir": prompts_dir,
        }
        defaults.update(overrides)
        return LoopConfig(**defaults)  # type: ignore[arg-type]

    return _factory


@pytest.fixture()
def sample_review_json() -> dict[str, object]:
    """Return a realistic review JSON matching the Codex output schema."""
    return {
        "findings": [
            {
                "title": "Unused import",
                "confidence_score": 0.95,
                "code_location": {
                    "file_path": "src/foo.py",
                    "line_range": {"start": 1, "end": 1},
                },
                "body": "The `os` module is imported but never used.",
            },
            {
                "title": "Missing null check",
                "confidence_score": 0.8,
                "code_location": {
                    "file_path": "src/bar.py",
                    "line_range": {"start": 42, "end": 45},
                },
                "body": "result may be None when the API returns 404.",
            },
        ],
        "overall_correctness": "patch is incorrect",
    }


@pytest.fixture()
def sample_review_json_str(sample_review_json: dict[str, object]) -> str:
    """Return sample_review_json as a compact JSON string."""
    return json.dumps(sample_review_json, separators=(",", ":"))
