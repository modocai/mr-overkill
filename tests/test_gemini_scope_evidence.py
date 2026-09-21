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
    assert "captured branch diff" in retry.call_args.kwargs["stdin"]


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
    assert "captured scoped diff" in retry.call_args.kwargs["stdin"]


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
        patch("mr_overkill.agents.retry_gemini_cmd", return_value=True) as retry,
    ):
        assert GeminiReviewAgent(config)(tmp_path / "out", 2)
    prompt = retry.call_args.kwargs["stdin"]
    assert "historical commit diff" in prompt
    assert "subsequent fix diff" in prompt


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
