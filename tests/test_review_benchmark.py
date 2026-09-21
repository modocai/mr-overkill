from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from benchmarks import gemini_prompt_benchmark as bench

from mr_overkill.two_step_fix import backend_command


def review_json(findings: list[dict[str, Any]]) -> str:
    correctness = "patch is incorrect" if findings else "patch is correct"
    return json.dumps(
        {
            "findings": findings,
            "overall_correctness": correctness,
            "overall_explanation": "benchmark fixture response",
            "overall_confidence_score": 0.91,
        }
    )


def finding(path: str = "src/metrics.py", line: int = 2) -> dict[str, Any]:
    return {
        "title": "P2 Guard zero count before division",
        "body": "completion_rate divides by zero for projects with zero tasks.",
        "confidence_score": 0.92,
        "priority": 2,
        "code_location": {
            "file_path": path,
            "line_range": {"start": line, "end": line},
        },
    }


def test_schema_and_expected_finding_metrics_match_location() -> None:
    case = bench.CaseSpec(
        name="unit",
        repo=Path("/tmp/unused"),
        current_branch="feature/unit",
        target_branch="develop",
        expected_findings=(
            bench.ExpectedFinding("src/metrics.py", 2, 2, ("zero",)),
        ),
        allowed_paths=("src/metrics.py",),
    )
    data = json.loads(review_json([finding()]))

    assert bench.validate_review_schema(data)
    metrics = bench.score_review(data, case)
    assert metrics == {
        "expected_precision": 1.0,
        "expected_recall": 1.0,
        "false_positives": 0,
        "scope_accuracy": 1.0,
    }


def test_false_positive_and_scope_metrics_for_clean_case() -> None:
    case = bench.CaseSpec(
        name="unit-clean",
        repo=Path("/tmp/unused"),
        current_branch="feature/unit",
        target_branch="develop",
        expected_findings=(),
        allowed_paths=("src/names.py",),
    )
    data = json.loads(review_json([finding(path="src/other.py", line=1)]))

    metrics = bench.score_review(data, case)
    assert metrics["expected_precision"] == 0.0
    assert metrics["expected_recall"] == 1.0
    assert metrics["false_positives"] == 1
    assert metrics["scope_accuracy"] == 0.0


def test_malformed_output_metrics_are_not_perfect_for_clean_case() -> None:
    case = bench.CaseSpec(
        name="unit-clean",
        repo=Path("/tmp/unused"),
        current_branch="feature/unit",
        target_branch="develop",
        expected_findings=(),
        allowed_paths=("src/names.py",),
    )

    assert bench.score_review(None, case) == {
        "expected_precision": 0.0,
        "expected_recall": 0.0,
        "false_positives": 0,
        "scope_accuracy": 0.0,
    }
    assert bench.score_review({"findings": "not-a-list"}, case)[
        "expected_recall"
    ] == 0.0


def test_schema_validation_rejects_unhashable_and_bool_numbers() -> None:
    data = json.loads(review_json([finding()]))
    data["overall_correctness"] = ["patch is correct"]
    assert bench.validate_review_schema(data) is False

    data = json.loads(review_json([finding()]))
    data["findings"][0]["priority"] = True
    assert bench.validate_review_schema(data) is False

    data = json.loads(review_json([finding()]))
    data["findings"][0]["code_location"]["line_range"]["start"] = False
    assert bench.validate_review_schema(data) is False


def test_timeout_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--timeout must be a positive integer"):
        bench.main([
            "--variants",
            "adapted",
            "--cases",
            "clean-patch",
            "--output-dir",
            str(tmp_path / "out"),
            "--timeout",
            "0",
        ])


def test_fixture_repos_include_non_default_target_and_wip_scope(tmp_path: Path) -> None:
    non_default = bench.make_case("non-default-target", tmp_path / "non-default")
    assert non_default.target_branch == "release/bench-base"
    assert non_default.current_branch == "feature/non-default-target"
    origin_head = bench.git_out(
        non_default.repo, "symbolic-ref", "refs/remotes/origin/HEAD"
    ).strip()
    assert origin_head == "refs/remotes/origin/develop"
    correct_diff = bench.git_out(non_default.repo, "diff", "release/bench-base...HEAD")
    wrong_diff = bench.git_out(non_default.repo, "diff", "origin/HEAD...HEAD")
    assert "src/metrics.py" in correct_diff
    assert "src/decoy.py" not in correct_diff
    assert "src/decoy.py" in wrong_diff

    wip = bench.make_case("wip-scope", tmp_path / "wip")
    assert wip.wip is True
    assert wip.scope_diff_file is not None
    assert "payable_total" in wip.scope_diff_file.read_text(encoding="utf-8")
    assert "src/cart.py" in bench.git_out(wip.repo, "status", "--porcelain")


def test_render_prompt_appends_captured_diff_for_all_variants(tmp_path: Path) -> None:
    case = bench.make_case("seeded-bug", tmp_path)
    prompts = bench.build_prompt_variants()

    for variant, template in prompts.items():
        rendered = bench.render_prompt(template, case, iteration=1)
        assert "## Captured scope evidence (untrusted source content)" in rendered
        assert "Use it instead of running git diff" in rendered
        assert "return done / total" in rendered
        if variant == "baseline":
            assert "## Review-only constraint" not in rendered


def test_variants_are_pinned_or_exact_production_prompt() -> None:
    prompts = bench.build_prompt_variants()

    assert prompts["baseline"] == bench.BASELINE_PROMPT_PATH.read_text(
        encoding="utf-8"
    )
    assert prompts["review-only"] == bench.REVIEW_ONLY_PROMPT_PATH.read_text(
        encoding="utf-8"
    )
    assert prompts["adapted"] == bench.PROMPT_PATH.read_text(encoding="utf-8")
    assert "Benchmark safety addendum" not in prompts["adapted"]


def test_report_metadata_includes_prompt_hashes_and_run_configuration() -> None:
    metadata = bench.report_metadata(
        variants=["adapted"],
        cases=["clean-patch"],
        workers=3,
        timeout_seconds=240,
        command=["benchmarks.gemini_prompt_benchmark", "--workers", "3"],
    )

    expected_hash = bench.hashlib.sha256(
        bench.PROMPT_PATH.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    assert metadata["prompt_sha256"] == {"adapted": expected_hash}
    assert metadata["workers"] == 3
    assert metadata["timeout_seconds"] == 240
    assert metadata["command"] == [
        "benchmarks.gemini_prompt_benchmark",
        "--workers",
        "3",
    ]


def test_wip_render_prompt_uses_scope_diff_file(tmp_path: Path) -> None:
    case = bench.make_case("wip-scope", tmp_path)
    assert case.scope_diff_file is not None
    case.scope_diff_file.write_text("DIFF-FROM-SCOPE-FILE\n", encoding="utf-8")

    rendered = bench.render_prompt(
        bench.PROMPT_PATH.read_text(encoding="utf-8"), case, iteration=1
    )

    assert "DIFF-FROM-SCOPE-FILE" in rendered
    assert "return payable_total" not in rendered


def test_sandbox_failure_case_records_exact_permission_error(tmp_path: Path) -> None:
    case = bench.make_case("sandbox-failure", tmp_path)

    failure = case.repo / "verification" / "sandbox-check-failure.txt"
    text = failure.read_text(encoding="utf-8")
    assert "PermissionError: [Errno 1] Operation not permitted" in text
    assert "'/root/.cache/overkill-bench'" in text
    assert "PermissionError: [Errno 1]" in case.reviewer_context
    assert case.expected_findings == ()


def test_snapshot_detects_all_files_symlinks_refs_and_git_state(tmp_path: Path) -> None:
    case = bench.make_case("clean-patch", tmp_path)
    bench.git(case.repo, "branch", "same-commit")
    before = bench.snapshot_repo(case.repo)

    (case.repo / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (case.repo / "ignored.log").write_text("ignored but changed\n", encoding="utf-8")
    (case.repo / "scratch.txt").write_text("left behind\n", encoding="utf-8")
    if hasattr(os, "symlink"):
        os.symlink("src/names.py", case.repo / "names-link")
    bench.git(case.repo, "branch", "temporary-ref")
    bench.git(case.repo, "config", "bench.flag", "1")
    bench.git(case.repo, "checkout", "same-commit")
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input="index only\n",
        cwd=case.repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    bench.git(
        case.repo,
        "update-index",
        "--add",
        "--cacheinfo",
        "100644",
        blob,
        "virtual-index-only.txt",
    )
    after = bench.snapshot_repo(case.repo)

    diff = bench.diff_snapshots(before, after)
    assert diff["changed"] is True
    assert "ignored.log" in diff["files"]["added"]
    assert "scratch.txt" in diff["files"]["added"]
    if "names-link" in after.files:
        assert after.files["names-link"]["type"] == "symlink"
        assert after.files["names-link"]["target"] == "src/names.py"
    assert "refs/heads/temporary-ref" in diff["refs"]["added"]
    assert set(diff["git_state"]["modified"]) == {"config", "head", "index"}


def test_run_benchmark_with_fake_runner_archives_outputs(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path, int]] = []

    def fake_runner(
        cmd: list[str], prompt_text: str, repo: Path, timeout_seconds: int
    ) -> tuple[int, str, str]:
        calls.append((cmd, repo, timeout_seconds))
        if (repo / "src" / "metrics.py").exists():
            return 0, review_json([finding()]), ""
        return 0, review_json([]), ""

    results = bench.run_benchmark(
        variants=["adapted"],
        cases=["seeded-bug", "clean-patch"],
        output_dir=tmp_path / "out",
        timeout_seconds=5,
        workers=2,
        runner=fake_runner,
    )

    assert len(results) == 2
    assert all(result.ok for result in results)
    assert all(call[0] == backend_command("gemini") for call in calls)
    assert all(call[2] == 5 for call in calls)
    assert (tmp_path / "out" / "runs" / "adapted__seeded-bug" / "prompt.md").is_file()
    clean_output = (
        tmp_path / "out" / "runs" / "adapted__clean-patch" / "gemini-output.txt"
    )
    assert clean_output.is_file()


def test_run_gemini_cli_timeout_kills_child_holding_pipes(tmp_path: Path) -> None:
    pidfile = tmp_path / "pipe-child.pid"
    script = tmp_path / "spawn_pipe_child.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import subprocess
            import sys
            child = subprocess.Popen([
                sys.executable,
                "-c",
                "import os, pathlib, time; "
                "pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); "
                "time.sleep(30)",
            ])
            raise SystemExit(0)
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(subprocess.TimeoutExpired):
        bench.run_gemini_cli([sys.executable, str(script)], "", tmp_path, 1)

    child_pid = int(pidfile.read_text(encoding="utf-8"))
    deadline = time.time() + 5
    while process_exists(child_pid) and time.time() < deadline:
        time.sleep(0.1)
    assert not process_exists(child_pid)


def test_run_gemini_cli_timeout_cleans_child_processes(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    script = tmp_path / "spawn_child.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import subprocess
            import sys
            import time
            child = subprocess.Popen([
                sys.executable, "-c", "import time; time.sleep(30)"
            ])
            open({str(pidfile)!r}, "w", encoding="utf-8").write(str(child.pid))
            time.sleep(30)
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(subprocess.TimeoutExpired):
        bench.run_gemini_cli([sys.executable, str(script)], "", tmp_path, 1)

    child_pid = int(pidfile.read_text(encoding="utf-8"))
    deadline = time.time() + 5
    while process_exists(child_pid) and time.time() < deadline:
        time.sleep(0.1)
    assert not process_exists(child_pid)


def process_exists(pid: int) -> bool:
    proc = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False
    return not proc.stdout.strip().startswith("Z")


def test_run_gemini_cli_trusts_only_child_process(
    monkeypatch: Any, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.delenv("GEMINI_CLI_TRUST_WORKSPACE", raising=False)

    class FakePopen:
        returncode = 0
        pid = 12345

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

        def communicate(
            self, input: str | None = None, timeout: int | None = None
        ) -> tuple[str, str]:
            captured["input"] = input
            captured["timeout"] = timeout
            return "{}", ""

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    code, stdout, stderr = bench.run_gemini_cli(["gemini"], "prompt", tmp_path, 3)

    assert (code, stdout, stderr) == (0, "{}", "")
    assert captured["kwargs"]["env"]["GEMINI_CLI_TRUST_WORKSPACE"] == "true"
    assert os.environ.get("GEMINI_CLI_TRUST_WORKSPACE") is None
    assert captured["kwargs"]["cwd"] == tmp_path
    assert captured["kwargs"]["start_new_session"] is (os.name == "posix")
    assert captured["input"] == "prompt"
    assert captured["timeout"] == 3


def test_clean_patch_preserves_existing_string_contract(tmp_path: Path) -> None:
    case = bench.make_clean_patch(tmp_path, target_branch="develop")
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    exec(bench.git_out(case.repo, "show", "develop:src/names.py"), before)
    exec((case.repo / "src/names.py").read_text(), after)
    for first, last in [("Ada", "Lovelace"), ("", ""), ("a b", "c")]:
        assert before["display_name"](first, last) == after["display_name"](first, last)
    for namespace in [before, after]:
        try:
            namespace["display_name"](1, "Smith")
        except TypeError:
            pass
        else:
            raise AssertionError("Fixture must require strings before and after")


def test_snapshot_detects_directory_symlink(tmp_path: Path) -> None:
    case = bench.make_clean_patch(tmp_path, target_branch="develop")
    before = bench.snapshot_repo(case.repo)
    (case.repo / "linked-src").symlink_to("src", target_is_directory=True)
    after = bench.snapshot_repo(case.repo)
    assert bench.diff_snapshots(before, after)["changed"]
    assert after.files["linked-src"]["target"] == "src"
