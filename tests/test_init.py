"""Tests for mr_overkill.init — project initialization."""

from __future__ import annotations

from pathlib import Path

from mr_overkill.init import init_project


class TestInitProject:
    def test_creates_directory_structure(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        rl = tmp_path / ".review-loop"
        assert rl.is_dir()
        assert (rl / "prompts" / "active").is_dir()
        assert (rl / "logs").is_dir()
        assert (rl / "logs" / "refactor").is_dir()

    def test_copies_prompt_templates(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        prompts = tmp_path / ".review-loop" / "prompts" / "active"
        prompt_files = sorted(p.name for p in prompts.iterdir())
        assert "codex-review.prompt.md" in prompt_files
        assert "claude-fix.prompt.md" in prompt_files
        assert "claude-self-review.prompt.md" in prompt_files
        assert len(prompt_files) == 10

    def test_copies_rc_files(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        rl = tmp_path / ".review-loop"
        assert (rl / ".reviewlooprc").is_file()
        assert (rl / ".refactorsuggestrc").is_file()

    def test_rc_content_matches_example(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        rc = tmp_path / ".review-loop" / ".reviewlooprc"
        content = rc.read_text()
        assert "TARGET_BRANCH" in content

    def test_creates_install_manifest(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        manifest = tmp_path / ".review-loop" / ".install-manifest"
        assert manifest.is_file()
        entries = manifest.read_text().strip().splitlines()
        assert any("prompts/active/" in e for e in entries)
        assert ".reviewlooprc" in entries
        assert ".refactorsuggestrc" in entries

    def test_updates_gitignore(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        gitignore = tmp_path / ".gitignore"
        assert gitignore.is_file()
        assert ".review-loop/" in gitignore.read_text().splitlines()

    def test_gitignore_appends_to_existing(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n")

        init_project(tmp_path)

        content = gitignore.read_text()
        assert "node_modules/" in content
        assert ".review-loop/" in content

    def test_gitignore_no_duplicate(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".review-loop/\n")

        init_project(tmp_path)

        content = gitignore.read_text()
        assert content.count(".review-loop/") == 1

    def test_reinit_preserves_user_rc(self, tmp_path: Path) -> None:
        """Re-running init should NOT overwrite user-edited RC files."""
        init_project(tmp_path)

        # Simulate user editing the RC file
        rc = tmp_path / ".review-loop" / ".reviewlooprc"
        rc.write_text("TARGET_BRANCH=main\n")

        init_project(tmp_path)

        assert rc.read_text() == "TARGET_BRANCH=main\n"

    def test_reinit_refreshes_prompts(self, tmp_path: Path) -> None:
        """Re-running init should overwrite prompt templates (tool-owned)."""
        init_project(tmp_path)

        prompts = tmp_path / ".review-loop" / "prompts" / "active"
        prompt = prompts / "codex-review.prompt.md"
        original = prompt.read_text()
        prompt.write_text("corrupted content\n")

        init_project(tmp_path)

        assert prompt.read_text() == original

    def test_idempotent(self, tmp_path: Path) -> None:
        """Running init twice should not error."""
        init_project(tmp_path)
        init_project(tmp_path)

        assert (tmp_path / ".review-loop" / "prompts" / "active").is_dir()
