"""Tests for Codex budget checker."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mr_overkill.budget import codex as codex_budget
from mr_overkill.budget.codex import (
    AUTH_MODE_APIKEY,
    AUTH_MODE_CHATGPT,
    AUTH_MODE_UNKNOWN,
    check_token_budget,
    codex_budget_sufficient,
    codex_home,
    detect_auth_mode,
    find_latest_token_count,
)
from mr_overkill.models import BudgetScope

# Captured before any test patches it, so the probe's own tests can restore it.
_REAL_LOGIN_STATUS_PROBE = codex_budget._login_status_auth_mode


@pytest.fixture(autouse=True)
def _no_login_status_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ``codex login status`` out of the tests that predate the probe.

    Detection falls through to it whenever nothing on disk names the login, so
    without this every such test would spawn the real CLI.  Tests that cover
    the probe restore it explicitly.
    """
    monkeypatch.setattr(codex_budget, "_login_status_auth_mode", lambda home: None)


def _write_session(
    sessions: Path,
    rate_limits: dict[str, object],
    timestamp: object = 1000,
) -> None:
    """Write a single token_count event into today's session directory."""
    day_dir = sessions / datetime.now(tz=UTC).strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {"type": "token_count", "rate_limits": rate_limits},
    }
    (day_dir / "session.jsonl").write_text(json.dumps(event) + "\n")


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


# ── auth-mode detection ──────────────────────────────────────────────


class TestDetectAuthMode:
    def test_no_auth_file_without_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert detect_auth_mode(tmp_path) == AUTH_MODE_UNKNOWN

    def test_explicit_apikey_mode(self, tmp_path: Path) -> None:
        (tmp_path / "auth.json").write_text(
            json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test"})
        )
        assert detect_auth_mode(tmp_path) == AUTH_MODE_APIKEY

    @pytest.mark.parametrize("spelling", ["api_key", "api-key", "APIKEY"])
    def test_apikey_spellings_are_normalized(
        self, tmp_path: Path, spelling: str
    ) -> None:
        # The gate compares against the canonical mode, so every spelling Codex
        # may write has to arrive there normalized.
        (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": spelling}))
        assert detect_auth_mode(tmp_path) == AUTH_MODE_APIKEY

    def test_explicit_chatgpt_mode(self, tmp_path: Path) -> None:
        (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}))
        assert detect_auth_mode(tmp_path) == AUTH_MODE_CHATGPT

    def test_infers_chatgpt_from_tokens(self, tmp_path: Path) -> None:
        (tmp_path / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "x"}})
        )
        assert detect_auth_mode(tmp_path) == AUTH_MODE_CHATGPT

    def test_infers_apikey_from_stored_key(self, tmp_path: Path) -> None:
        (tmp_path / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-test"}))
        assert detect_auth_mode(tmp_path) == AUTH_MODE_APIKEY

    def test_env_fallback_when_no_auth_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert detect_auth_mode(tmp_path) == AUTH_MODE_APIKEY

    def test_codex_api_key_overrides_chatgpt_auth_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "sk-test")
        (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}))
        assert detect_auth_mode(tmp_path) == AUTH_MODE_APIKEY

    def test_openai_api_key_loses_to_chatgpt_auth_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex keeps using the cached ChatGPT login here, so plan rate limits
        # still apply and the gate has to stay active.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}))
        assert detect_auth_mode(tmp_path) == AUTH_MODE_CHATGPT

    def test_config_login_method_when_credentials_are_in_the_keyring(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No auth.json at all: Codex stored the login in the OS keyring.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (tmp_path / "config.toml").write_text(
            'cli_auth_credentials_store = "keyring"\nforced_login_method = "api"\n'
        )
        assert detect_auth_mode(tmp_path) == AUTH_MODE_APIKEY

    def test_legacy_config_auth_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (tmp_path / "config.toml").write_text('preferred_auth_method = "apikey"\n')
        assert detect_auth_mode(tmp_path) == AUTH_MODE_APIKEY

    def test_config_chatgpt_login_keeps_gate_active(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (tmp_path / "config.toml").write_text('forced_login_method = "chatgpt"\n')
        assert detect_auth_mode(tmp_path) == AUTH_MODE_CHATGPT

    def test_openai_api_key_loses_to_config_chatgpt_login(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Keyring-backed ChatGPT login: the config declares it explicitly, so
        # an OPENAI_API_KEY left in the environment for other tools must not
        # switch the gate off.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        (tmp_path / "config.toml").write_text(
            'cli_auth_credentials_store = "keyring"\nforced_login_method = "chatgpt"\n'
        )
        assert detect_auth_mode(tmp_path) == AUTH_MODE_CHATGPT

    def test_auth_file_wins_over_config_login_method(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # config.toml only records intent; a cached ChatGPT login is what Codex
        # actually sends, so plan rate limits still apply.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}))
        (tmp_path / "config.toml").write_text('forced_login_method = "api"\n')
        assert detect_auth_mode(tmp_path) == AUTH_MODE_CHATGPT

    def test_malformed_config_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (tmp_path / "config.toml").write_text("not = = toml")
        assert detect_auth_mode(tmp_path) == AUTH_MODE_UNKNOWN

    def test_malformed_auth_file_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (tmp_path / "auth.json").write_text("{not json")
        assert detect_auth_mode(tmp_path) == AUTH_MODE_UNKNOWN

    def test_codex_home_respects_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_HOME", "/custom/codex")
        assert codex_home() == Path("/custom/codex")


# ── keyring logins, read back from ``codex login status`` ────────────


def _stub_login_status(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    returncode: int = 0,
    stream: str = "stderr",
) -> None:
    """Make ``codex login status`` report ``status`` without running Codex.

    Real Codex prints the status line on stderr, so that is the default.
    """

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd == ["codex", "login", "status"]
        return subprocess.CompletedProcess(
            cmd,
            returncode,
            status if stream == "stdout" else "",
            status if stream == "stderr" else "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)


class TestLoginStatusProbe:
    @pytest.fixture(autouse=True)
    def _use_real_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Put the real probe back: these tests are about the probe itself, and
        # stub ``subprocess.run`` instead of the function under test.
        monkeypatch.setattr(
            codex_budget, "_login_status_auth_mode", _REAL_LOGIN_STATUS_PROBE
        )

    @pytest.mark.parametrize("stream", ["stderr", "stdout"])
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("Logged in using an API key - sk-proj-***abcde", AUTH_MODE_APIKEY),
            ("Logged in using Amazon Bedrock API key", AUTH_MODE_APIKEY),
            ("Logged in using ChatGPT", AUTH_MODE_CHATGPT),
        ],
    )
    def test_reports_the_active_login(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        status: str,
        expected: str,
        stream: str,
    ) -> None:
        # A keyring-backed login leaves nothing on disk to read.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        _stub_login_status(monkeypatch, status, stream=stream)
        assert detect_auth_mode(tmp_path) == expected

    def test_a_warning_ahead_of_the_status_line_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex may print a warning first; the status line still has to be read.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        _stub_login_status(
            monkeypatch,
            "warning: config.toml: unknown key `foo`\n"
            "Logged in using an API key - sk-proj-***abcde",
        )
        assert detect_auth_mode(tmp_path) == AUTH_MODE_APIKEY

    def test_chatgpt_login_beats_an_ambient_openai_api_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The gate must not be switched off by the environment when Codex
        # itself says the keyring holds a ChatGPT login.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        _stub_login_status(monkeypatch, "Logged in using ChatGPT")
        assert detect_auth_mode(tmp_path) == AUTH_MODE_CHATGPT

    @pytest.mark.parametrize(
        ("status", "returncode"),
        [
            ("Not logged in", 1),
            ("Logged in using access token", 0),
            ("", 0),
        ],
    )
    def test_unreadable_logins_keep_the_gate_active(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        status: str,
        returncode: int,
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        _stub_login_status(monkeypatch, status, returncode)
        assert detect_auth_mode(tmp_path) == AUTH_MODE_UNKNOWN

    def test_missing_codex_binary_keeps_the_gate_active(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        def raise_oserror(cmd: list[str], **kwargs: object) -> None:
            raise OSError("codex not found")

        monkeypatch.setattr(subprocess, "run", raise_oserror)
        assert detect_auth_mode(tmp_path) == AUTH_MODE_UNKNOWN

    def test_config_login_method_wins_over_the_probe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # config.toml forces the login method, so there is nothing to ask.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (tmp_path / "config.toml").write_text('forced_login_method = "chatgpt"\n')
        _stub_login_status(monkeypatch, "Logged in using an API key - sk-x")
        assert detect_auth_mode(tmp_path) == AUTH_MODE_CHATGPT

    def test_probe_runs_against_the_given_codex_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        seen: dict[str, str] = {}

        def fake_run(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            env = kwargs["env"]
            assert isinstance(env, dict)
            seen.update(env)
            return subprocess.CompletedProcess(cmd, 0, "", "Logged in using ChatGPT")

        monkeypatch.setattr(subprocess, "run", fake_run)
        detect_auth_mode(tmp_path)

        assert seen["CODEX_HOME"] == str(tmp_path)


class TestApiKeyMode:
    def test_ignores_stale_session_logs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pre-API-key ChatGPT session log must not gate API-key runs."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "apikey"}))
        sessions = tmp_path / "sessions"
        now = int(datetime.now(tz=UTC).timestamp())
        _write_session(
            sessions,
            {
                "primary": {
                    "used_percent": 90.0,
                    "window_minutes": 10080,
                    "resets_at": now + 86400,
                },
            },
        )

        status = check_token_budget(sessions)

        assert status.mode == AUTH_MODE_APIKEY
        assert status.five_hour_used_pct is None
        assert status.seven_day_used_pct is None
        assert codex_budget_sufficient(BudgetScope.MICRO, status) is True
        assert codex_budget_sufficient(BudgetScope.MODULE, status) is True

    def test_chatgpt_mode_still_reads_session_logs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}))
        sessions = tmp_path / "sessions"
        now = int(datetime.now(tz=UTC).timestamp())
        _write_session(
            sessions,
            {
                "primary": {
                    "used_percent": 80.0,
                    "window_minutes": 300,
                    "resets_at": now + 3600,
                },
            },
        )

        status = check_token_budget(sessions)

        assert status.mode == "session_log"
        assert status.five_hour_used_pct == 80


# ── window mapping by declared length ────────────────────────────────


class TestWindowMapping:
    def test_weekly_primary_maps_to_seven_day(self, tmp_path: Path) -> None:
        """Codex sends the weekly limit as `primary` on some plans."""
        sessions = tmp_path / "sessions"
        now = int(datetime.now(tz=UTC).timestamp())
        _write_session(
            sessions,
            {
                "primary": {
                    "used_percent": 90.0,
                    "window_minutes": 10080,
                    "resets_at": now + 86400,
                },
                "secondary": None,
            },
        )

        status = check_token_budget(sessions)

        assert status.seven_day_used_pct == 90
        assert status.five_hour_used_pct is None
        assert status.seven_day_resets_at is not None
        # 7-day at 90 % does not block micro scope — only module and above.
        assert codex_budget_sufficient(BudgetScope.MICRO, status) is True
        assert codex_budget_sufficient(BudgetScope.MODULE, status) is False

    def test_swapped_windows_are_reordered(self, tmp_path: Path) -> None:
        sessions = tmp_path / "sessions"
        now = int(datetime.now(tz=UTC).timestamp())
        _write_session(
            sessions,
            {
                "primary": {
                    "used_percent": 70.0,
                    "window_minutes": 10080,
                    "resets_at": now + 86400,
                },
                "secondary": {
                    "used_percent": 20.0,
                    "window_minutes": 300,
                    "resets_at": now + 3600,
                },
            },
        )

        status = check_token_budget(sessions)

        assert status.five_hour_used_pct == 20
        assert status.seven_day_used_pct == 70

    def test_missing_window_minutes_uses_position(self, tmp_path: Path) -> None:
        sessions = tmp_path / "sessions"
        now = int(datetime.now(tz=UTC).timestamp())
        _write_session(
            sessions,
            {
                "primary": {"used_percent": 30.0, "resets_at": now + 3600},
                "secondary": {"used_percent": 60.0, "resets_at": now + 86400},
            },
        )

        status = check_token_budget(sessions)

        assert status.five_hour_used_pct == 30
        assert status.seven_day_used_pct == 60

    def test_two_windows_same_kind_keeps_higher(self, tmp_path: Path) -> None:
        sessions = tmp_path / "sessions"
        now = int(datetime.now(tz=UTC).timestamp())
        _write_session(
            sessions,
            {
                "primary": {
                    "used_percent": 40.0,
                    "window_minutes": 10080,
                    "resets_at": now + 86400,
                },
                "secondary": {
                    "used_percent": 65.0,
                    "window_minutes": 20160,
                    "resets_at": now + 86400,
                },
            },
        )

        status = check_token_budget(sessions)

        assert status.seven_day_used_pct == 65
        assert status.five_hour_used_pct is None
