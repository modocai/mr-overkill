"""Tests for Codex budget checker."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from mr_overkill.budget.codex import check_token_budget, find_latest_token_count


class TestFindLatestTokenCount:
    def test_no_sessions_dir(self, tmp_path: Path) -> None:
        assert find_latest_token_count(tmp_path / "nonexistent") is None

    def test_empty_sessions_dir(self, tmp_path: Path) -> None:
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        assert find_latest_token_count(sessions) is None

    def test_finds_token_count_event(self, tmp_path: Path) -> None:
        sessions = tmp_path / "sessions"
        now = datetime.now(tz=UTC)
        day_dir = sessions / now.strftime("%Y/%m/%d")
        day_dir.mkdir(parents=True)

        event = {
            "type": "event_msg",
            "timestamp": 1000,
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {"used_percent": 45.0, "resets_at": 9999},
                },
            },
        }
        (day_dir / "session.jsonl").write_text(json.dumps(event) + "\n")

        result = find_latest_token_count(sessions)
        assert result is not None
        assert result["payload"]["type"] == "token_count"

    def test_picks_latest_by_timestamp(self, tmp_path: Path) -> None:
        sessions = tmp_path / "sessions"
        now = datetime.now(tz=UTC)
        day_dir = sessions / now.strftime("%Y/%m/%d")
        day_dir.mkdir(parents=True)

        events = [
            {
                "type": "event_msg",
                "timestamp": 100,
                "payload": {
                    "type": "token_count",
                    "rate_limits": {"primary": {"used_percent": 10.0}},
                },
            },
            {
                "type": "event_msg",
                "timestamp": 200,
                "payload": {
                    "type": "token_count",
                    "rate_limits": {"primary": {"used_percent": 50.0}},
                },
            },
        ]
        content = "\n".join(json.dumps(e) for e in events) + "\n"
        (day_dir / "session.jsonl").write_text(content)

        result = find_latest_token_count(sessions)
        assert result is not None
        assert result["timestamp"] == 200


class TestCheckTokenBudget:
    def test_no_data(self, tmp_path: Path) -> None:
        status = check_token_budget(tmp_path / "nonexistent")
        assert status.mode == "no_data"
        assert status.five_hour_used_pct is None
        assert status.seven_day_used_pct is None

    def test_with_session_data(self, tmp_path: Path) -> None:
        sessions = tmp_path / "sessions"
        now = datetime.now(tz=UTC)
        day_dir = sessions / now.strftime("%Y/%m/%d")
        day_dir.mkdir(parents=True)

        event = {
            "type": "event_msg",
            "timestamp": 1000,
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {
                        "used_percent": 45.0,
                        "resets_at": int(now.timestamp()) + 3600,
                    },
                },
            },
        }
        (day_dir / "session.jsonl").write_text(json.dumps(event) + "\n")

        status = check_token_budget(sessions)
        assert status.mode == "session_log"
        assert status.five_hour_used_pct == 45
        assert status.resets_at is not None
