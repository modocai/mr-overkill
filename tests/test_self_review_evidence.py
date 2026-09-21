"""Regression coverage for ignored self-review evidence and its lifetime."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from mr_overkill.self_review import self_review_subloop
from mr_overkill.two_step_fix import backend_command


@pytest.mark.parametrize("backend", ["gemini", "agy", "codex", "claude"])
@pytest.mark.parametrize("outcome", ["clean", "failed", "exception", "unverified"])
def test_self_review_evidence(
    tmp_path: Path, backend: str, outcome: str,
) -> None:
    log_dir = tmp_path / ".overkill" / "logs"
    log_dir.mkdir(parents=True)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "claude-self-review.prompt.md").write_text("$DIFF_FILE")
    content = "diff --git a/calc.py b/calc.py\n+return 0\n"
    seen: list[Path] = []

    def generate(changed: list[str], output: Path, cwd: Path | None) -> None:
        output.write_text(content)

    def retry(output: Path, label: str, command: list[str], **kw: object) -> bool:
        evidence = Path(str(kw["stdin"]))
        seen.append(evidence)
        assert evidence.read_text() == content
        if backend in {"gemini", "agy"}:
            flag = "--add-dir" if backend == "agy" else "--include-directories"
            assert command == [*backend_command(backend), flag, str(evidence.parent)]
            assert evidence.is_absolute()
            assert tmp_path not in evidence.parents
        else:
            assert command == backend_command(backend)
            assert evidence == log_dir / "diff-1-1.diff"
        if outcome == "exception":
            raise RuntimeError("provider failed")
        if outcome == "failed":
            return False
        output.write_text(json.dumps({
            "findings": [], "overall_correctness": "patch is correct",
            "overall_confidence_score": 0 if outcome == "unverified" else 0.95,
        }))
        return True

    def run() -> str:
        return self_review_subloop(
            [], 1, log_dir, 1, "{}", retry_fn=retry,
            budget_fn=Mock(return_value=True), fix_fn=Mock(),
            prompts_dir=prompts, current_branch="fix", target_branch="develop",
            backend=backend,
        )

    with (
        patch("mr_overkill.self_review.changed_files_since_snapshot",
              return_value=["calc.py"]),
        patch("mr_overkill.self_review._generate_diff", side_effect=generate),
    ):
        if outcome == "exception":
            with pytest.raises(RuntimeError, match="provider failed"):
                run()
        else:
            result = run()
            if outcome == "failed":
                assert "self-review failed" in result
            elif outcome == "unverified" and backend in {"gemini", "agy"}:
                assert "unverified" in result
                assert "passed" not in result
            else:
                assert "passed" in result

    assert len(seen) == 1
    if backend in {"gemini", "agy"}:
        assert not seen[0].parent.exists()
    assert (log_dir / "diff-1-1.diff").read_text() == content
