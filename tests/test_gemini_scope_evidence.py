"""Plan-mode reviewers receive evidence without needing shell permission."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill.agents import GeminiReviewAgent
from mr_overkill.models import LoopConfig


def config_for(path: Path, **kwargs: object) -> LoopConfig:
    (path / "gemini-review.prompt.md").write_text(
        "Review ${CURRENT_BRANCH} ${REVIEW_SCOPE_NOTE}"
    )
    config = LoopConfig(
        "feature", "release-base", 1, reviewer_backend="gemini",
        prompts_dir=path, log_dir=path, skip_budget_gate=True,
    )
    for key, value in kwargs.items():
        setattr(config, key, value)
    return config


def test_branch_evidence_uses_requested_target_without_external_diff(tmp_path: Path):
    config = config_for(tmp_path)
    with (
        patch("mr_overkill.agents.subprocess.run", return_value=MagicMock(
            returncode=0, stdout="captured branch diff",
        )) as git,
        patch("mr_overkill.agents.retry_gemini_cmd", return_value=True) as retry,
    ):
        assert GeminiReviewAgent(config)(tmp_path / "out", 1)
    assert git.call_args.args[0] == [
        "git", "diff", "--no-ext-diff", "--no-textconv", "-U5",
        "release-base...feature", "--",
    ]
    assert (tmp_path / "out.diff").read_text() == "captured branch diff"
    assert "Captured evidence file:" in retry.call_args.kwargs["stdin"]


@pytest.mark.parametrize("wip", [False, True])
def test_scope_artifact_does_not_require_reviewer_shell(tmp_path: Path, wip: bool):
    diff = tmp_path / "scope.diff"
    diff.write_text("captured scoped diff")
    config = config_for(tmp_path, scope_diff_file=diff, wip=wip)
    with (
        patch("mr_overkill.agents.subprocess.run") as git,
        patch("mr_overkill.agents.retry_gemini_cmd", return_value=True) as retry,
    ):
        assert GeminiReviewAgent(config)(tmp_path / "out", 1)
    git.assert_not_called()
    assert (tmp_path / "out.diff").read_text() == "captured scoped diff"
    assert "Captured evidence file:" in retry.call_args.kwargs["stdin"]


@pytest.mark.parametrize("missing_artifact", [False, True])
def test_missing_scope_evidence_fails_before_model(
    tmp_path: Path, missing_artifact: bool,
) -> None:
    config = config_for(tmp_path)
    if missing_artifact:
        config.scope_diff_file = tmp_path / "missing.diff"
    with (
        patch("mr_overkill.agents.subprocess.run", return_value=MagicMock(
            returncode=1, stdout="", stderr="bad revision",
        )),
        patch("mr_overkill.agents.retry_gemini_cmd") as retry,
    ):
        assert not GeminiReviewAgent(config)(tmp_path / "out", 1)
    retry.assert_not_called()


def test_commit_followup_includes_fix_diff(tmp_path: Path) -> None:
    diff = tmp_path / "scope.diff"
    diff.write_text("historical commit diff")
    config = config_for(tmp_path, scope_diff_file=diff, scope_commit="a" * 40)
    with (
        patch("mr_overkill.agents._format_review_scope", return_value="commit scope"),
        patch("mr_overkill.agents.subprocess.run", return_value=MagicMock(
            returncode=0, stdout="subsequent fix diff",
        )),
        patch("mr_overkill.agents.retry_gemini_cmd", return_value=True),
    ):
        assert GeminiReviewAgent(config)(tmp_path / "out", 2)
    evidence = (tmp_path / "out.diff").read_text()
    assert "historical commit diff" in evidence
    assert "subsequent fix diff" in evidence


@pytest.mark.parametrize("scope", ["review", "micro", "module", "layer", "full"])
def test_bundled_google_guidance_preserves_output_and_scope(scope: str) -> None:
    name = "gemini-review" if scope == "review" else f"gemini-refactor-{scope}"
    text = (Path(__file__).parents[1] / "prompts" / "active" /
            f"{name}.prompt.md").read_text()
    assert "Do NOT modify" in text
    assert "P0" in text and "P3" in text
    assert '"findings"' in text
    assert "origin/HEAD" in text
    assert "invoke /code-review" in text
    if scope == "review":
        assert "${REVIEW_SCOPE_NOTE}" in text
        assert "${TARGET_BRANCH}...${CURRENT_BRANCH}" in text
    else:
        assert "${SOURCE_FILES_PATH}" in text


@pytest.mark.parametrize("backend", ["gemini", "agy"])
def test_large_diff_is_read_from_file_not_argv(tmp_path: Path, backend: str) -> None:
    config = config_for(tmp_path, reviewer_backend=backend)
    diff = "+large diff evidence\n" * 160_000
    output = tmp_path / "review.json"
    output.write_text('{"findings": []}')
    with (
        patch("mr_overkill.agents.subprocess.run", return_value=MagicMock(
            returncode=0, stdout=diff,
        )),
        patch("mr_overkill.agents.retry_gemini_cmd", return_value=True) as retry,
    ):
        assert GeminiReviewAgent(config)(output, 1)
    command = retry.call_args.args[2]
    prompt = command[-1] if backend == "agy" else retry.call_args.kwargs["stdin"]
    assert len(prompt) < 10_000
    evidence = output.with_suffix(".diff")
    flag = "--add-dir" if backend == "agy" else "--include-directories"
    readable_dir = Path(command[command.index(flag) + 1])
    assert str(readable_dir / "evidence.txt") in prompt
    assert not readable_dir.exists()
    assert evidence.read_text() == diff
    assert "+large diff evidence" not in prompt


def test_wip_followup_preserves_draft_and_supplies_current_evidence(tmp_path: Path):
    original = tmp_path / "wip.diff"
    original.write_text("author draft")
    config = config_for(tmp_path, wip=True, scope_diff_file=original)
    with (
        patch("mr_overkill.agents.subprocess.run", side_effect=[
            MagicMock(returncode=0, stdout="current tracked draft and fixes"),
            MagicMock(returncode=0, stdout="new helper.py\0draft.py\0"),
        ]) as git,
        patch("mr_overkill.agents.retry_gemini_cmd", return_value=True) as retry,
    ):
        assert GeminiReviewAgent(config)(tmp_path / "review.json", 2)
    assert git.call_args_list[0].args[0][-2:] == ["HEAD", "--"]
    assert git.call_args_list[1].args[0] == [
        "git", "ls-files", "--others", "--exclude-standard", "-z",
    ]
    evidence = (tmp_path / "review.diff").read_text()
    assert "author draft" in evidence
    assert "current tracked draft and fixes" in evidence
    assert '"new helper.py"' in evidence
    assert original.read_text() == "author draft"
    assert "frozen draft snapshot" in retry.call_args.kwargs["stdin"]


def test_wip_followup_does_not_review_incomplete_capture(tmp_path: Path):
    original = tmp_path / "wip.diff"
    original.write_text("author draft")
    config = config_for(tmp_path, wip=True, scope_diff_file=original)
    with (
        patch("mr_overkill.agents.subprocess.run", side_effect=[
            MagicMock(returncode=1, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]),
        patch("mr_overkill.agents.retry_gemini_cmd") as retry,
    ):
        assert not GeminiReviewAgent(config)(tmp_path / "review.json", 2)
    retry.assert_not_called()


@pytest.mark.parametrize("backend,flag", [
    ("gemini", "--include-directories"), ("agy", "--add-dir"),
])
def test_evidence_workspace_is_scoped_and_removed(backend: str, flag: str):
    from mr_overkill.agents import _google_review_evidence

    with _google_review_evidence(backend, "scoped evidence") as (path, command):
        assert path.read_text() == "scoped evidence"
        assert command[command.index(flag) + 1] == str(path.parent)
        assert ".overkill" not in path.parts
        assert "plan" in command
    assert not path.parent.exists()


def test_zero_confidence_no_findings_is_not_success(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    output = tmp_path / "review.json"
    output.write_text(
        '{"findings": [], "overall_correctness": "patch is correct", '
        '"overall_explanation": "Could not read evidence", '
        '"overall_confidence_score": 0.0}'
    )
    with (
        patch("mr_overkill.agents.subprocess.run", return_value=MagicMock(
            returncode=0, stdout="diff",
        )),
        patch("mr_overkill.agents.retry_gemini_cmd", return_value=True),
    ):
        assert not GeminiReviewAgent(config)(output, 1)


def test_refactor_inventory_failure_stops_before_provider(tmp_path: Path) -> None:
    from mr_overkill.agents import GeminiRefactorReviewAgent

    config = config_for(tmp_path)
    with (
        patch("mr_overkill.agents.subprocess.run", return_value=MagicMock(
            returncode=1, stdout="",
        )),
        patch("mr_overkill.agents.retry_gemini_cmd") as retry,
    ):
        assert not GeminiRefactorReviewAgent(config, "module")(tmp_path / "out", 1)
    retry.assert_not_called()
