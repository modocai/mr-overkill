"""Tests for mr_overkill.json_extract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mr_overkill.json_extract import (
    extract_json_from_file,
    inject_refactoring_plan,
    normalize_paths,
    parse_review_json,
)

# ── extract_json_from_file ────────────────────────────────────────────


class TestExtractJsonFromFile:
    """Tests for the 3-tier extraction pipeline."""

    def test_valid_json_direct_parse(self, tmp_path: Path) -> None:
        """Tier 1: plain JSON file is parsed directly."""
        data = {"findings": [], "overall_correctness": "all clear"}
        p = tmp_path / "review.json"
        p.write_text(json.dumps(data))
        assert extract_json_from_file(p) == data

    def test_jsonl_returns_last_object(self, tmp_path: Path) -> None:
        """Tier 1 JSONL: when a file has multiple JSON objects, return the last."""
        first = {"iteration": 1}
        second = {"iteration": 2}
        p = tmp_path / "multi.json"
        p.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
        assert extract_json_from_file(p) == second

    def test_fenced_code_block(self, tmp_path: Path) -> None:
        """Tier 2: JSON inside a fenced code block is extracted."""
        data = {"status": "ok"}
        content = (
            "Here is the review:\n"
            "```json\n"
            f"{json.dumps(data, indent=2)}\n"
            "```\n"
            "That's all.\n"
        )
        p = tmp_path / "fenced.md"
        p.write_text(content)
        assert extract_json_from_file(p) == data

    def test_fenced_code_block_no_language(self, tmp_path: Path) -> None:
        """Tier 2: fenced block without a language tag works too."""
        data = {"value": 42}
        content = "Some text\n```\n" + json.dumps(data) + "\n```\n"
        p = tmp_path / "fenced_plain.md"
        p.write_text(content)
        assert extract_json_from_file(p) == data

    def test_regex_fallback(self, tmp_path: Path) -> None:
        """Tier 3: JSON embedded in free text is extracted via regex."""
        data = {"found": True}
        content = "Random preamble text\n" + json.dumps(data) + "\nmore text\n"
        p = tmp_path / "mixed.txt"
        p.write_text(content)
        assert extract_json_from_file(p) == data

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        """Empty (or whitespace-only) file returns None."""
        p = tmp_path / "empty.json"
        p.write_text("   \n  \n")
        assert extract_json_from_file(p) is None

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        """Non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            extract_json_from_file(tmp_path / "no_such_file.json")

    def test_garbage_content_returns_none(self, tmp_path: Path) -> None:
        """Completely non-JSON content returns None."""
        p = tmp_path / "garbage.txt"
        p.write_text("This is not JSON at all, no braces here.")
        assert extract_json_from_file(p) is None


# ── parse_review_json ─────────────────────────────────────────────────


class TestParseReviewJson:
    """Tests for the warning-emitting wrapper."""

    def test_success(self, tmp_path: Path) -> None:
        data = {"findings": []}
        p = tmp_path / "review.json"
        p.write_text(json.dumps(data))
        result, rc = parse_review_json(p, "review")
        assert rc == 0
        assert result == data

    def test_file_not_found(self, tmp_path: Path) -> None:
        result, rc = parse_review_json(tmp_path / "missing.json", "review")
        assert rc == 2
        assert result is None

    def test_parse_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("   ")
        result, rc = parse_review_json(p, "review")
        assert rc == 1
        assert result is None


# ── normalize_paths ───────────────────────────────────────────────────


class TestNormalizePaths:
    """Tests for absolute → repo-relative path stripping."""

    def test_strips_repo_root_prefix(self) -> None:
        review = {
            "findings": [
                {
                    "title": "Bug",
                    "code_location": {
                        "file_path": "/home/user/repo/src/foo.py",
                    },
                },
            ],
        }
        result = normalize_paths(review, "/home/user/repo")
        loc = result["findings"][0]["code_location"]
        assert loc["file_path"] == "src/foo.py"

    def test_strips_absolute_file_path_key(self) -> None:
        review = {
            "findings": [
                {
                    "title": "Bug",
                    "code_location": {
                        "absolute_file_path": "/repo/src/bar.py",
                    },
                },
            ],
        }
        result = normalize_paths(review, "/repo")
        loc = result["findings"][0]["code_location"]
        assert loc["file_path"] == "src/bar.py"
        assert "absolute_file_path" not in loc

    def test_no_findings_returns_unchanged(self) -> None:
        review = {"overall_correctness": "ok"}
        assert normalize_paths(review, "/repo") == review

    def test_already_relative_unchanged(self) -> None:
        review = {
            "findings": [
                {
                    "title": "Bug",
                    "code_location": {"file_path": "src/foo.py"},
                },
            ],
        }
        result = normalize_paths(review, "/some/other/root")
        assert result["findings"][0]["code_location"]["file_path"] == "src/foo.py"


# ── inject_refactoring_plan ──────────────────────────────────────────


class TestInjectRefactoringPlan:
    """Tests for refactoring plan injection."""

    def test_injects_plan(self) -> None:
        review: dict[str, object] = {"findings": []}
        plan: dict[str, object] = {"scope": "module", "steps": ["a", "b"]}
        result = inject_refactoring_plan(review, plan)
        assert result["refactoring_plan"] == plan
        # Original should still have findings
        assert result["findings"] == []

    def test_none_plan_returns_unchanged(self) -> None:
        review: dict[str, object] = {"findings": []}
        result = inject_refactoring_plan(review, None)
        assert result is review  # identity — no copy needed

    def test_replaces_existing_plan(self) -> None:
        review: dict[str, object] = {
            "findings": [],
            "refactoring_plan": {"old": True},
        }
        new_plan: dict[str, object] = {"new": True}
        result = inject_refactoring_plan(review, new_plan)
        assert result["refactoring_plan"] == new_plan
