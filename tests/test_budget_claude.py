"""Tests for Claude budget checker."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mr_overkill.budget import BudgetScope, budget_sufficient
from mr_overkill.budget.claude import (
    UNKNOWN_TIER,
    check_local,
    check_token_budget,
    detect_tier,
)
from mr_overkill.models import BudgetStatus


class TestDetectTier:
    def test_no_telemetry_dir(self, tmp_path: Path) -> None:
        # Not "pro". Defaulting to the smallest plan turns "I don't know"
        # into a 995% reading on a max20 account.
        assert detect_tier(tmp_path / "nonexistent") is None

    def test_empty_telemetry_dir(self, tmp_path: Path) -> None:
        telemetry = tmp_path / "telemetry"
        telemetry.mkdir()
        assert detect_tier(telemetry) is None

    def test_telemetry_without_the_key(self, tmp_path: Path) -> None:
        """Regression: the shape the telemetry actually has now.

        ``user_attributes`` stopped appearing in these files, which is what
        made every lookup miss and every account look like ``pro``.
        """
        telemetry = tmp_path / "telemetry"
        telemetry.mkdir()
        (telemetry / "1p_failed_events.abc.json").write_text(
            json.dumps({
                "event_type": "ClaudeCodeInternalEvent",
                "event_data": {
                    "event_name": "tengu_event_loop_stall",
                    "client_timestamp": "2026-08-28T01:16:18.117Z",
                    "model": "claude-opus-5",
                    "auth": {"account_uuid": "f0689dd4"},
                },
            })
        )
        assert detect_tier(telemetry) is None

    def test_unrecognised_tier_value(self, tmp_path: Path) -> None:
        # A plan the map has never heard of — "team", say — is not "pro".
        telemetry = tmp_path / "telemetry"
        telemetry.mkdir()
        (telemetry / "event.json").write_text(
            json.dumps({
                "event_data": {
                    "client_timestamp": "2026-01-01T00:00:00Z",
                    "user_attributes": {"rateLimitTier": "some_new_plan"},
                }
            })
        )
        assert detect_tier(telemetry) is None

    def test_pro_tier(self, tmp_path: Path) -> None:
        # The plain "default" plan is Pro. It used to resolve through the
        # catch-all rather than the map, so removing the catch-all dropped it.
        telemetry = tmp_path / "telemetry"
        telemetry.mkdir()
        (telemetry / "event.json").write_text(
            json.dumps({
                "event_data": {
                    "client_timestamp": "2025-01-01T00:00:00Z",
                    "user_attributes": {"rateLimitTier": "default"},
                }
            })
        )
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


class TestLocalEstimateWithoutATier:
    """Without a tier there is no denominator, so there is no percentage."""

    def _session(self, projects: Path, weighted_tokens: int) -> None:
        session = projects / "proj" / "session.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text(json.dumps({
            "type": "assistant",
            "timestamp": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "message": {
                "id": "msg_1",
                "usage": {"input_tokens": weighted_tokens, "output_tokens": 0},
            },
        }))

    def test_tokens_used_but_no_tier_reports_no_percentage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "mr_overkill.budget.claude.detect_tier", lambda *a, **k: None
        )
        projects = tmp_path / "projects"
        self._session(projects, 19_909_525)

        status = check_local(projects)

        # The incident: this used to be 995%, from a limit nobody verified.
        assert status.five_hour_used_pct is None
        assert status.tokens_used == 19_909_525
        assert status.tier == UNKNOWN_TIER

    def test_no_tokens_still_reports_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Nothing used needs no denominator, so an unknown tier costs nothing.
        monkeypatch.setattr(
            "mr_overkill.budget.claude.detect_tier", lambda *a, **k: None
        )
        projects = tmp_path / "projects"
        projects.mkdir()

        assert check_local(projects).five_hour_used_pct == 0

    def test_budget_gate_treats_no_percentage_as_go(self) -> None:
        # budget_sufficient's existing "no data — assume OK" branch is what
        # makes returning None safe rather than merely honest.
        status = BudgetStatus(
            five_hour_used_pct=None, seven_day_used_pct=None, tokens_used=1,
            mode="local", tier=UNKNOWN_TIER, resets_at=None, estimated=True,
        )
        assert budget_sufficient(BudgetScope.MICRO, status) is True


class TestCachedOAuthMerge:
    """A cached OAuth reading is real data; the local estimate may not be."""

    _CACHED = BudgetStatus(
        five_hour_used_pct=44, seven_day_used_pct=49, tokens_used=0,
        mode="oauth", tier="pro", resets_at=None, estimated=False,
    )

    def _run(
        self, local: BudgetStatus, monkeypatch: pytest.MonkeyPatch
    ) -> BudgetStatus:
        monkeypatch.setattr(
            "mr_overkill.budget.claude.check_oauth", lambda: None
        )
        monkeypatch.setattr(
            "mr_overkill.budget.claude._load_cache", lambda: self._CACHED
        )
        monkeypatch.setattr(
            "mr_overkill.budget.claude.check_local", lambda: local
        )
        return check_token_budget()

    def test_a_percentageless_local_estimate_cannot_override_the_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The incident, end to end.

        OAuth was rate-limited, the tier could not be detected, and the local
        estimate's 995% replaced a cached 44% — while the log still said it
        was using cached OAuth data.
        """
        local = BudgetStatus(
            five_hour_used_pct=None, seven_day_used_pct=None,
            tokens_used=19_909_525, mode="local", tier=UNKNOWN_TIER,
            resets_at=None, estimated=True,
        )
        assert self._run(local, monkeypatch).five_hour_used_pct == 44

    def test_a_higher_trustworthy_local_estimate_still_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The behaviour the merge exists for: a stale cache must not
        # under-report when the local estimate is usable.
        local = BudgetStatus(
            five_hour_used_pct=70, seven_day_used_pct=None, tokens_used=7,
            mode="local", tier="max20", resets_at=None, estimated=True,
        )
        assert self._run(local, monkeypatch).five_hour_used_pct == 70

    def test_a_lower_local_estimate_does_not_pull_the_cache_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        local = BudgetStatus(
            five_hour_used_pct=5, seven_day_used_pct=None, tokens_used=7,
            mode="local", tier="max20", resets_at=None, estimated=True,
        )
        assert self._run(local, monkeypatch).five_hour_used_pct == 44
