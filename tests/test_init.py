"""Tests for mr_overkill.init — project initialization."""

from __future__ import annotations

from pathlib import Path

from mr_overkill.init import init_project


class TestInitProject:
    def test_creates_directory_structure(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        ok = tmp_path / ".overkill"
        assert ok.is_dir()
        assert (ok / "prompts" / "active").is_dir()
        assert (ok / "logs").is_dir()
        assert (ok / "logs" / "refactor").is_dir()

    def test_copies_prompt_templates(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        prompts = tmp_path / ".overkill" / "prompts" / "active"
        prompt_files = sorted(p.name for p in prompts.iterdir())
        assert "codex-review.prompt.md" in prompt_files
        assert "claude-fix.prompt.md" in prompt_files
        assert "claude-self-review.prompt.md" in prompt_files
        assert len(prompt_files) == 15

    def test_copies_rc_files(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        ok = tmp_path / ".overkill"
        assert (ok / ".overkillrc").is_file()
        assert (ok / ".refactorsuggestrc").is_file()

    def test_rc_content_matches_example(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        rc = tmp_path / ".overkill" / ".overkillrc"
        content = rc.read_text()
        assert "TARGET_BRANCH" in content

    def test_creates_install_manifest(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        manifest = tmp_path / ".overkill" / ".install-manifest"
        assert manifest.is_file()
        entries = manifest.read_text().strip().splitlines()
        assert any("prompts/active/" in e for e in entries)
        assert ".overkillrc" in entries
        assert ".refactorsuggestrc" in entries

    def test_updates_gitignore(self, tmp_path: Path) -> None:
        init_project(tmp_path)

        gitignore = tmp_path / ".gitignore"
        assert gitignore.is_file()
        assert ".overkill/" in gitignore.read_text().splitlines()

    def test_gitignore_appends_to_existing(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n")

        init_project(tmp_path)

        content = gitignore.read_text()
        assert "node_modules/" in content
        assert ".overkill/" in content

    def test_gitignore_no_duplicate(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".overkill/\n")

        init_project(tmp_path)

        content = gitignore.read_text()
        assert content.count(".overkill/") == 1

    def test_reinit_preserves_user_rc(self, tmp_path: Path) -> None:
        """Re-running init should NOT overwrite user-edited RC files."""
        init_project(tmp_path)

        # Simulate user editing the RC file
        rc = tmp_path / ".overkill" / ".overkillrc"
        rc.write_text("TARGET_BRANCH=main\n")

        init_project(tmp_path)

        assert rc.read_text() == "TARGET_BRANCH=main\n"

    def test_reinit_refreshes_prompts(self, tmp_path: Path) -> None:
        """Re-running init should overwrite prompt templates (tool-owned)."""
        init_project(tmp_path)

        prompts = tmp_path / ".overkill" / "prompts" / "active"
        prompt = prompts / "codex-review.prompt.md"
        original = prompt.read_text()
        prompt.write_text("corrupted content\n")

        init_project(tmp_path)

        assert prompt.read_text() == original

    def test_idempotent(self, tmp_path: Path) -> None:
        """Running init twice should not error."""
        init_project(tmp_path)
        init_project(tmp_path)

        assert (tmp_path / ".overkill" / "prompts" / "active").is_dir()

    def test_migrate_legacy_dir(self, tmp_path: Path) -> None:
        """Init migrates .review-loop/ to .overkill/ automatically."""
        # Set up legacy directory
        legacy = tmp_path / ".review-loop"
        legacy.mkdir()
        (legacy / ".reviewlooprc").write_text("TARGET_BRANCH=main\n")
        (legacy / ".refactorsuggestrc").write_text("SCOPE=auto\n")
        (legacy / "logs").mkdir()

        init_project(tmp_path)

        # Legacy dir should be gone, new dir should exist
        assert not legacy.is_dir()
        ok = tmp_path / ".overkill"
        assert ok.is_dir()
        # RC file should be renamed
        assert (ok / ".overkillrc").is_file()
        assert (ok / ".overkillrc").read_text() == "TARGET_BRANCH=main\n"
        assert (ok / ".refactorsuggestrc").is_file()

    def test_migrate_gitignore_legacy_marker(self, tmp_path: Path) -> None:
        """Init replaces .review-loop/ marker with .overkill/ in .gitignore."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(
            "node_modules/\n"
            "# review-loop (added by overkill init)\n"
            ".review-loop/\n"
        )

        init_project(tmp_path)

        content = gitignore.read_text()
        assert ".overkill/" in content
        assert ".review-loop/" not in content
