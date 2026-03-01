"""Tests for Claude budget checker."""

from __future__ import annotations

import json
from pathlib import Path

from mr_overkill.budget.claude import check_local, detect_tier


class TestDetectTier:
    def test_no_telemetry_dir(self, tmp_path: Path) -> None:
        assert detect_tier(tmp_path / "nonexistent") == "pro"

    def test_empty_telemetry_dir(self, tmp_path: Path) -> None:
        telemetry = tmp_path / "telemetry"
        telemetry.mkdir()
        assert detect_tier(telemetry) == "pro"

    def test_max5_tier(self, tmp_path: Path) -> None:
        telemetry = tmp_path / "telemetry"
        telemetry.mkdir()
        (telemetry / "event.json").write_text(
            json.dumps({
                "event_data": {
                    "client_timestamp": "2025-01-01T00:00:00Z",
                    "user_attributes": json.dumps({
                        "rateLimitTier": "default_claude_max_5x"
                    }),
                }
            })
        )
        assert detect_tier(telemetry) == "max5"

    def test_max20_tier(self, tmp_path: Path) -> None:
        telemetry = tmp_path / "telemetry"
        telemetry.mkdir()
        (telemetry / "event.json").write_text(
            json.dumps({
                "event_data": {
                    "client_timestamp": "2025-01-01T00:00:00Z",
                    "user_attributes": {
                        "rateLimitTier": "default_claude_max_20x"
                    },
                }
            })
        )
        assert detect_tier(telemetry) == "max20"

    def test_picks_most_recent(self, tmp_path: Path) -> None:
        telemetry = tmp_path / "telemetry"
        telemetry.mkdir()
        # Older event: max5
        (telemetry / "old.json").write_text(
            json.dumps({
                "event_data": {
                    "client_timestamp": "2025-01-01T00:00:00Z",
                    "user_attributes": json.dumps({
                        "rateLimitTier": "default_claude_max_5x"
                    }),
                }
            })
        )
        # Newer event: max20
        (telemetry / "new.json").write_text(
            json.dumps({
                "event_data": {
                    "client_timestamp": "2025-06-01T00:00:00Z",
                    "user_attributes": json.dumps({
                        "rateLimitTier": "default_claude_max_20x"
                    }),
                }
            })
        )
        assert detect_tier(telemetry) == "max20"


class TestCheckLocal:
    def test_no_projects_dir(self, tmp_path: Path) -> None:
        status = check_local(tmp_path / "nonexistent")
        assert status.mode == "local"
        assert status.five_hour_used_pct == 0
        assert status.tokens_used == 0
        assert status.estimated is True

    def test_empty_projects_dir(self, tmp_path: Path) -> None:
        projects = tmp_path / "projects"
        projects.mkdir()
        status = check_local(projects)
        assert status.five_hour_used_pct == 0
        assert status.tokens_used == 0
