"""Role selection, permission boundaries, and assessment handoff regressions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill.agents import (
    _RetryFn,
    create_fix_agent,
    create_self_review_agent,
)
from mr_overkill.cli import parse_refactor_suggest_args, parse_review_loop_args
from mr_overkill.models import LoopConfig
from mr_overkill.two_step_fix import backend_command, claude_two_step_fix


@pytest.mark.parametrize("refactor", [False, True])
@pytest.mark.parametrize("backend", ["claude", "codex", "gemini", "agy"])
def test_role_cli_overrides_rc(tmp_path: Path, refactor: bool, backend: str) -> None:
    with (
        patch("mr_overkill.cli._load_rc_file", return_value={
            "FIXER_BACKEND": "invalid", "SELF_REVIEWER_BACKEND": "invalid",
        }),
        patch("mr_overkill.cli._detect_current_branch", return_value="feat/test"),
        patch("mr_overkill.cli._detect_pr_number", return_value=None),
        patch("mr_overkill.cli.subprocess.run", return_value=MagicMock(
            returncode=0, stdout=str(tmp_path),
        )),
    ):
        args = ["-n", "1", "--fixer-backend", backend,
                "--self-reviewer-backend", "gemini", "--reviewer-backend", "codex"]
        config = (parse_refactor_suggest_args(args)[0] if refactor
                  else parse_review_loop_args(args))
    assert config.fixer_backend == backend
    assert config.reviewer_backend == "codex"
    assert config.self_reviewer_backend == "gemini"


@pytest.mark.parametrize("refactor", [False, True])
@pytest.mark.parametrize("key", ["FIXER_BACKEND", "SELF_REVIEWER_BACKEND"])
def test_invalid_role_rc(tmp_path: Path, refactor: bool, key: str) -> None:
    with (
        patch("mr_overkill.cli._load_rc_file", return_value={key: "typo"}),
        patch("mr_overkill.cli._detect_current_branch", return_value="feat/test"),
        patch("mr_overkill.cli._detect_pr_number", return_value=None),
        patch("mr_overkill.cli.subprocess.run", return_value=MagicMock(
            returncode=0, stdout=str(tmp_path),
        )),
        pytest.raises(SystemExit),
    ):
        if refactor:
            parse_refactor_suggest_args(["-n", "1"])
        else:
            parse_review_loop_args(["-n", "1"])


@pytest.mark.parametrize("backend", ["claude", "codex", "gemini", "agy"])
@pytest.mark.parametrize("override", [None, "gemini"])
def test_self_review_follows_fixer(backend: str, override: str | None) -> None:
    config = LoopConfig(
        "feat/test", "develop", 1,
        fixer_backend=backend, self_reviewer_backend=override,
    )
    with patch("mr_overkill.agents.self_review_subloop", return_value="") as run:
        create_self_review_agent(config, create_fix_agent(config))(
            [], 1, Path("logs"), 1, '{}',
        )
    assert run.call_args.kwargs["backend"] == (override or backend)


@pytest.mark.parametrize("backend", ["codex", "gemini", "agy"])
@pytest.mark.parametrize("missing_opinion", [False, True])
def test_two_step_handoff(
    tmp_path: Path, backend: str, missing_opinion: bool,
) -> None:
    (tmp_path / "claude-fix.prompt.md").write_text("Findings: $REVIEW_JSON")
    (tmp_path / "claude-fix-execute.prompt.md").write_text("Apply agreed fixes.")
    opinion = tmp_path / "opinion.md"
    if not missing_opinion:
        opinion.write_text("Fix A; skip B.")
    retry = MagicMock(return_value=True)
    budget = MagicMock(return_value=True)
    ok = claude_two_step_fix(
        '{"findings": ["A", "B"]}', opinion, tmp_path / "fix.md", "1",
        retry_fn=retry, budget_fn=budget, prompts_dir=tmp_path,
        current_branch="feat/test", target_branch="develop", backend=backend,
    )
    assert ok is not missing_opinion
    assert retry.call_args_list[0].args[2] == backend_command(backend)
    assert all(call.args[0] == backend for call in budget.call_args_list)
    if missing_opinion:
        assert retry.call_count == 1
    else:
        execution = retry.call_args_list[1]
        assert execution.args[2] == backend_command(backend, edit=True)
        assert "Fix A; skip B." in execution.kwargs["stdin"]
        assert '"findings"' in execution.kwargs["stdin"]
        assert "Apply agreed fixes." in execution.kwargs["stdin"]


@pytest.mark.parametrize("backend", ["codex", "gemini"])
def test_retry_dispatch(tmp_path: Path, backend: str) -> None:
    config = LoopConfig(
        "feat/test", "develop", 1, retry_max_wait=12, retry_initial_wait=3,
    )
    output = tmp_path / "result.md"
    with patch(f"mr_overkill.agents.retry_{backend}_cmd", return_value=True) as run:
        assert _RetryFn(config)(output, "test", backend_command(backend), stdin="body")
    assert run.call_args.kwargs["stdin"] == "body"
    assert run.call_args.kwargs["max_wait"] == 12
    if backend == "codex":
        assert run.call_args.args[2][-2:] == ["-o", str(output)]


def test_permission_modes() -> None:
    assert "read-only" in backend_command("codex")
    assert "workspace-write" in backend_command("codex", edit=True)
    assert "plan" in backend_command("gemini")
    assert "yolo" in backend_command("gemini", edit=True)
    assert "--sandbox" in backend_command("gemini", edit=True)


@pytest.mark.parametrize("refactor", [False, True])
@pytest.mark.parametrize("override", [False, True])
def test_resume_preserves_inheritance(
    tmp_path: Path, refactor: bool, override: bool,
) -> None:
    (tmp_path / "max-loop.txt").write_text("1")
    (tmp_path / "fixer-backend.txt").write_text("codex")
    (tmp_path / "self-reviewer-backend.txt").write_text("")
    with (
        patch("mr_overkill.cli._resolve_log_dir", return_value=tmp_path),
        patch("mr_overkill.cli._load_rc_file", return_value={
            "FIXER_BACKEND": "claude", "SELF_REVIEWER_BACKEND": "gemini",
        }),
        patch("mr_overkill.cli._detect_current_branch", return_value="feat/test"),
        patch("mr_overkill.cli._detect_pr_number", return_value=None),
        patch("mr_overkill.cli.subprocess.run", return_value=MagicMock(
            returncode=0, stdout=str(tmp_path),
        )),
    ):
        args = ["--resume"] + (["--fixer-backend", "agy"] if override else [])
        config = (parse_refactor_suggest_args(args)[0] if refactor
                  else parse_review_loop_args(args))
    assert config.fixer_backend == ("agy" if override else "codex")
    assert config.self_reviewer_backend is None


def test_agy_retry_uses_print_argument(tmp_path: Path) -> None:
    config = LoopConfig("feat/test", "develop", 1)
    (tmp_path / "output").write_text("Done")
    with patch("mr_overkill.agents.retry_gemini_cmd", return_value=True) as run:
        assert _RetryFn(config)(
            tmp_path / "output", "agy fix", backend_command("agy", edit=True),
            stdin="Review these findings.",
        )
    cmd = run.call_args.args[2]
    assert cmd[0] == "agy"
    assert cmd[-2:] == ["-p", "Review these findings."]
    assert "accept-edits" in cmd
    assert "--dangerously-skip-permissions" not in cmd
    assert "plan" in backend_command("agy")


@pytest.mark.parametrize("refactor", [False, True])
def test_agy_reviewer(tmp_path: Path, refactor: bool) -> None:
    from mr_overkill.agents import create_review_agent

    config = LoopConfig(
        "feat/test", "develop", 1, reviewer_backend="agy",
        prompts_dir=tmp_path, log_dir=tmp_path, skip_budget_gate=True,
    )
    (tmp_path / "review.json").write_text('{"findings": []}')
    for name in ("gemini-review.prompt.md", "gemini-refactor-module.prompt.md"):
        (tmp_path / name).write_text("Review $CURRENT_BRANCH")
    with (
        patch("mr_overkill.agents.retry_gemini_cmd", return_value=True) as run,
        patch("mr_overkill.agents.subprocess.run", return_value=MagicMock(stdout="")),
    ):
        reviewer = create_review_agent(config, scope="module" if refactor else None)
        assert reviewer(tmp_path / "review.json", 1)
    cmd = run.call_args.args[2]
    assert cmd[0] == "agy"
    assert "plan" in cmd
    assert "--approval-mode" not in cmd


def test_codex_retry_pipes_prompt(tmp_path: Path) -> None:
    from mr_overkill.retry import retry_codex_cmd

    with patch("mr_overkill.retry.subprocess.run", return_value=MagicMock(
        returncode=0,
    )) as run:
        assert retry_codex_cmd(
            tmp_path / "stderr", "opinion", backend_command("codex"), stdin="body",
        )
    assert run.call_args.kwargs["input"] == "body"
    assert run.call_args.kwargs["stdin"] is None


@pytest.mark.parametrize("exists", [False, True])
def test_agy_empty_success_is_failure(tmp_path: Path, exists: bool) -> None:
    output = tmp_path / "result"
    if exists:
        output.write_text("  ")
    config = LoopConfig("feat/test", "develop", 1)
    with patch("mr_overkill.agents.retry_gemini_cmd", return_value=True):
        assert not _RetryFn(config)(output, "review", backend_command("agy"))


@pytest.mark.parametrize("override", [None, "claude"])
def test_chained_review_preserves_roles(override: str | None) -> None:
    from mr_overkill.__main__ import main

    config = LoopConfig(
        "feat/test", "develop", 1, reviewer_backend="agy", fixer_backend="codex",
        self_reviewer_backend=override,
    )
    extra = MagicMock(with_review=True, review_loops=2, create_pr=False)
    with (
        patch("sys.argv", ["overkill", "refactor-suggest"]),
        patch(
            "mr_overkill.cli.parse_refactor_suggest_args", return_value=(config, extra),
        ),
        patch("mr_overkill.refactor_suggest.run", return_value=0),
        patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="1")),
        patch("mr_overkill.cli.parse_review_loop_args", return_value=config) as parse,
        patch("mr_overkill.review_loop.run", return_value=0),
        pytest.raises(SystemExit) as exc,
    ):
        main()
    assert exc.value.code == 0
    argv = parse.call_args.args[0]
    assert argv[argv.index("--fixer-backend") + 1] == "codex"
    assert argv[argv.index("--self-reviewer-backend") + 1] == (override or "codex")


@pytest.mark.parametrize("refactor", [False, True])
@pytest.mark.parametrize("self_backend", [None, "gemini"])
def test_real_rc_file_role_selection(
    tmp_path: Path, refactor: bool, self_backend: str | None,
) -> None:
    workspace = tmp_path / ".overkill"
    workspace.mkdir()
    filename = ".refactorsuggestrc" if refactor else ".overkillrc"
    content = 'FIXER_BACKEND="agy"\nREVIEWER_BACKEND="codex"\n'
    if self_backend:
        content += f'SELF_REVIEWER_BACKEND="{self_backend}"\n'
    (workspace / filename).write_text(content)
    with (
        patch("mr_overkill.cli._detect_current_branch", return_value="feat/test"),
        patch("mr_overkill.cli._detect_pr_number", return_value=None),
        patch("mr_overkill.cli.subprocess.run", return_value=MagicMock(
            returncode=0, stdout=str(tmp_path),
        )),
    ):
        config = (parse_refactor_suggest_args(["-n", "1"])[0] if refactor
                  else parse_review_loop_args(["-n", "1"]))
    assert config.fixer_backend == "agy"
    assert config.self_reviewer_backend == self_backend
    assert config.reviewer_backend == "codex"
