"""Parallel review execution, deterministic aggregation, and fail-closed behavior."""

import json
import sys
import threading
import time
from concurrent.futures import CancelledError, Future
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill.agents import ParallelReviewAgent, create_review_agent
from mr_overkill.loop_engine import review_fix_loop
from mr_overkill.models import (
    BudgetScope,
    BudgetTimeoutError,
    FinalStatus,
    LoopConfig,
    LoopResult,
    parse_reviewer_backends,
)
from mr_overkill.refactor_suggest import run as run_refactor
from mr_overkill.retry import (
    retry_codex_cmd,
    review_cancellation,
    wait_for_budget,
)


def config_at(path: Path) -> LoopConfig:
    return LoopConfig("feat/test", "develop", 1, log_dir=path,
                      reviewer_backend="gemini,claude,codex")


def review(findings: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"findings": findings or [], "overall_correctness":
            "patch is incorrect" if findings else "patch is correct"}


def test_parallel_calls_isolated_and_joined(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    barrier = threading.Barrier(3, timeout=5)
    seen: list[tuple[str, Path]] = []

    def factory(child: LoopConfig, *, scope: str | None = None) -> MagicMock:
        def run(path: Path, iteration: int) -> bool:
            assert iteration == 2
            assert path.parent == child.log_dir
            assert child.target_branch == config.target_branch
            assert scope == "layer"
            seen.append((child.reviewer_backend, path))
            barrier.wait()  # Sequential execution cannot pass this barrier.
            path.write_text(json.dumps(review([{"title": child.reviewer_backend}])))
            return True
        return MagicMock(side_effect=run)

    output = tmp_path / "review-2.json"
    with patch("mr_overkill.agents.create_review_agent", side_effect=factory):
        assert ParallelReviewAgent(config, "layer")(output, 2)
    assert len({path for _, path in seen}) == 3
    merged = json.loads(output.read_text())
    assert [f["title"] for f in merged["findings"]] == ["gemini", "claude", "codex"]
    assert merged["overall_correctness"] == "patch is incorrect"
    assert config.log_dir == tmp_path


@pytest.mark.parametrize("failure", [
    "false", "exception", "budget", "missing", "invalid", "findings", "verdict", "plan",
])
def test_any_failed_reviewer_discards_aggregate(tmp_path: Path, failure: str) -> None:
    config = config_at(tmp_path)
    output = tmp_path / "review-1.json"
    output.write_text(json.dumps(review()))
    stale = tmp_path / "reviewers" / "claude" / output.name
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps(review()))
    completed = []

    def factory(child: LoopConfig, **kw: object) -> MagicMock:
        def run(path: Path, iteration: int) -> bool:
            completed.append(child.reviewer_backend)
            if child.reviewer_backend == "claude":
                if failure == "false":
                    return False
                if failure == "exception":
                    raise RuntimeError("CLI crashed")
                if failure == "budget":
                    raise BudgetTimeoutError("quota")
                if failure == "missing":
                    return True
                data = {
                    "invalid": "not JSON", "findings": '{"findings": {}}',
                    "verdict": '{"findings": [], "overall_correctness": "unknown"}',
                    "plan": json.dumps({**review(), "refactoring_plan": {"steps": 1}}),
                }[failure]
                path.write_text(data)
            else:
                path.write_text(json.dumps(review()))
            return True
        return MagicMock(side_effect=run)

    with patch("mr_overkill.agents.create_review_agent", side_effect=factory):
        assert not ParallelReviewAgent(config)(output, 1)
    assert not output.exists()
    assert set(completed) == {"gemini", "claude", "codex"}


@pytest.mark.parametrize("different", [False, True])
def test_exact_deduplication_preserves_distinct_findings(
    tmp_path: Path, different: bool,
) -> None:
    def factory(child: LoopConfig, **kw: object) -> MagicMock:
        def run(path: Path, iteration: int) -> bool:
            path.write_text(json.dumps(review([{
                "title": "[P1] Bug", "body": (
                    child.reviewer_backend if different else "Explanation"
                ), "code_location": {"file_path": "a.py"},
            }])))
            return True
        return MagicMock(side_effect=run)
    output = tmp_path / "review-1.json"
    with patch("mr_overkill.agents.create_review_agent", side_effect=factory):
        assert ParallelReviewAgent(config_at(tmp_path))(output, 1)
    findings = json.loads(output.read_text())["findings"]
    assert len(findings) == (3 if different else 1)
    if not different:
        assert "gemini, claude, codex" in findings[0]["body"]


@pytest.mark.parametrize("negative", [False, True])
def test_all_clear_requires_unanimity(tmp_path: Path, negative: bool) -> None:
    def factory(child: LoopConfig, **kw: object) -> MagicMock:
        def run(path: Path, iteration: int) -> bool:
            data = review()
            if negative and child.reviewer_backend == "codex":
                data["overall_correctness"] = "patch is incorrect"
            path.write_text(json.dumps(data))
            return True
        return MagicMock(side_effect=run)
    output = tmp_path / "review-1.json"
    with patch("mr_overkill.agents.create_review_agent", side_effect=factory):
        assert ParallelReviewAgent(config_at(tmp_path))(output, 1)
    assert json.loads(output.read_text())["overall_correctness"] == (
        "patch is incorrect" if negative else "patch is correct"
    )


def test_refactor_plans_are_preserved(tmp_path: Path) -> None:
    def factory(child: LoopConfig, **kw: object) -> MagicMock:
        def run(path: Path, iteration: int) -> bool:
            path.write_text(json.dumps({
                **review([{"title": child.reviewer_backend}]),
                "overall_correctness": "needs refactoring",
                "refactoring_plan": {
                    "scope": "layer", "summary": child.reviewer_backend,
                    "estimated_blast_radius": "low",
                    "steps": [{"order": 1, "description": "Fix", "files": ["a.py"]}],
                },
            }))
            return True
        return MagicMock(side_effect=run)
    output = tmp_path / "review-1.json"
    with patch("mr_overkill.agents.create_review_agent", side_effect=factory):
        assert ParallelReviewAgent(config_at(tmp_path), "layer")(output, 1)
    plan = json.loads(output.read_text())["refactoring_plan"]
    assert [step["order"] for step in plan["steps"]] == [1, 2, 3]
    assert all(name in plan["summary"] for name in ["gemini", "claude", "codex"])


def test_factory_preserves_single_backend_path(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    assert isinstance(create_review_agent(config), ParallelReviewAgent)
    config.reviewer_backend = "gemini, gemini"
    with patch("mr_overkill.agents.GeminiReviewAgent") as single:
        assert create_review_agent(config) is single.return_value
        assert single.call_args.args[0].reviewer_backend == "gemini"
    assert parse_reviewer_backends(" gemini,claude,gemini,codex ") == [
        "gemini", "claude", "codex",
    ]


def test_failed_parallel_review_never_runs_fixer(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    fixer = MagicMock()
    with (
        patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[]),
        patch("mr_overkill.loop_engine._validate_target_branch", return_value=True),
        patch("mr_overkill.loop_engine._no_diff", return_value=False),
        patch("mr_overkill.loop_engine._save_metadata"),
        patch("mr_overkill.agents.create_review_agent", return_value=MagicMock(
            return_value=False,
        )),
    ):
        result = review_fix_loop(
            config, reviewer=ParallelReviewAgent(config), fixer=fixer, cwd=tmp_path,
        )
    assert result.final_status == FinalStatus.REVIEW_FAILED
    fixer.assert_not_called()


def test_combined_findings_reach_fixer_once(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    config.auto_commit = False
    fixer = MagicMock(return_value=True)

    def factory(child: LoopConfig, **kw: object) -> MagicMock:
        def run(path: Path, iteration: int) -> bool:
            path.write_text(json.dumps(review([{"title": child.reviewer_backend}])))
            return True
        return MagicMock(side_effect=run)

    with (
        patch("mr_overkill.loop_engine._reject_dirty_worktree", return_value=[]),
        patch("mr_overkill.loop_engine._validate_target_branch", return_value=True),
        patch("mr_overkill.loop_engine._no_diff", return_value=False),
        patch("mr_overkill.loop_engine._save_metadata"),
        patch("mr_overkill.loop_engine.diff_hash", return_value="hash"),
        patch("mr_overkill.loop_engine.stash_allowlisted", return_value=False),
        patch("mr_overkill.loop_engine.snapshot_worktree", return_value=[]),
        patch("mr_overkill.agents.create_review_agent", side_effect=factory),
    ):
        result = review_fix_loop(
            config, reviewer=ParallelReviewAgent(config), fixer=fixer, cwd=tmp_path,
        )
    assert result.final_status == FinalStatus.AUTO_COMMIT_DISABLED
    fixer.assert_called_once()
    assert len(json.loads(fixer.call_args.args[0])["findings"]) == 3


@pytest.mark.parametrize("dry_run", [False, True])
def test_auto_scope_checks_all_reviewers(tmp_path: Path, dry_run: bool) -> None:
    config = config_at(tmp_path)
    config.current_branch = "refactor/module-test"
    config.dry_run = dry_run
    config.fixer_backend = "agy"
    with (
        patch("mr_overkill.refactor_suggest.git_all_dirty", return_value=[]),
        patch("mr_overkill.refactor_suggest.subprocess.run", return_value=MagicMock(
            returncode=0, stdout="src/a.py\n",
        )),
        patch("mr_overkill.refactor_suggest.resolve_auto_scope", return_value="module")
        as auto_scope,
        patch("mr_overkill.refactor_suggest.review_fix_loop", return_value=LoopResult(
            final_status=FinalStatus.ALL_CLEAR, iterations_run=1,
        )),
    ):
        assert run_refactor(config, "auto") == 0
    assert auto_scope.call_args.kwargs["tools"] == (
        ["gemini", "claude", "codex"] if dry_run else
        ["agy", "gemini", "claude", "codex"]
    )


def test_keyboard_interrupt_cancels_budget_waits(tmp_path: Path) -> None:
    started = threading.Event()
    stopped = threading.Event()

    def agent(path: Path, iteration: int) -> bool:
        started.set()
        try:
            return wait_for_budget(
                lambda *_: False, "codex", BudgetScope.MICRO,
            )
        finally:
            stopped.set()

    def interrupt(*args: object, **kwargs: object) -> None:
        assert started.wait(2)
        raise KeyboardInterrupt

    start = time.monotonic()
    with (
        patch("mr_overkill.agents.create_review_agent", return_value=agent),
        patch.object(Future, "result", side_effect=interrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        ParallelReviewAgent(config_at(tmp_path))(tmp_path / "review-1.json", 1)
    assert stopped.is_set()
    assert time.monotonic() - start < 5


@pytest.mark.parametrize("backoff", [False, True])
def test_cancel_stops_running_cli_and_retry_sleep(
    tmp_path: Path, backoff: bool,
) -> None:
    cancel = threading.Event()
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    start = time.monotonic()
    code = (
        "import sys; print('rate limit', file=sys.stderr); sys.exit(1)" if backoff else
        "import time; time.sleep(30)"
    )
    try:
        with review_cancellation(cancel), pytest.raises(CancelledError):
            retry_codex_cmd(
                tmp_path / "cli.stderr", "test", [sys.executable, "-c", code],
            )
    finally:
        timer.cancel()
        timer.join()
    assert time.monotonic() - start < 5


def test_parallel_cli_stdin_survives_communication_polling(tmp_path: Path) -> None:
    code = "import sys,time; time.sleep(.4); print(sys.stdin.read(), file=sys.stderr)"
    with review_cancellation(threading.Event()):
        assert retry_codex_cmd(
            tmp_path / "cli.stderr", "test", [sys.executable, "-c", code],
            stdin="prompt content",
        )
    assert (tmp_path / "cli.stderr").read_text().strip() == "prompt content"


@pytest.mark.parametrize("name", [
    "gemini-review.prompt.md",
    *[f"gemini-refactor-{scope}.prompt.md"
      for scope in ("micro", "module", "layer", "full")],
])
def test_gemini_reviewer_prompts_forbid_edits(name: str) -> None:
    prompt = Path(__file__).parents[1] / "prompts" / "active" / name
    text = prompt.read_text()
    assert "This invocation is review-only. Do NOT modify" in text
    assert "a separate\nfixer will apply them" in text
