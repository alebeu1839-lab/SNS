"""ストレージ層（同意・分離・削除・選択保存）の検証。"""
from __future__ import annotations

from worklens.storage.db import to_utc_iso


def test_consent_toggle_records_revocation(repos, org):
    repos.set_consent(org["company_id"], org["user_id"], org["device_id"], "browser_usage", False)
    consents = {c["scope_key"]: c for c in repos.list_consents(org["user_id"])}
    revoked = consents["browser_usage"]
    assert revoked["enabled"] == 0
    assert revoked["revoked_at"] is not None
    assert repos.consent_map(org["user_id"], org["device_id"])["browser_usage"] is False


def test_company_data_is_isolated(repos, org):
    other_company = repos.create_company("別会社")
    other_user = repos.create_user(other_company, "x@y.jp", "他社ユーザー")
    other_device = repos.register_device(other_company, other_user, "PC-X", "macOS", "0.1.0")
    session = repos.start_session(
        other_company, other_user, other_device, "2025-05-01T00:00:00+00:00", "0.1.0"
    )
    repos.insert_events(
        [{"company_id": other_company, "session_id": session, "ts": "2025-05-01T00:00:01+00:00",
          "event_type": "app_focus", "scope_key": "app_usage", "app_name": "EXCEL.EXE"}]
    )
    assert repos.count_events(other_company) == 1
    assert repos.count_events(org["company_id"]) == 0


def test_purge_user_data_removes_events_and_sessions(repos, collected):
    assert repos.count_events(collected["company_id"]) > 0
    counts = repos.purge_user_data(collected["user_id"])
    assert counts["events"] > 0
    assert repos.count_events(collected["company_id"]) == 0
    assert repos.list_sessions(collected["user_id"]) == []


def test_decision_is_upserted_not_duplicated(repos, analyzed):
    candidate = repos.list_candidates(analyzed["run_id"])[0]
    repos.set_decision(
        analyzed["company_id"], candidate["id"], candidate["task_id"],
        analyzed["user_id"], "automate", "最初の判断",
    )
    repos.set_decision(
        analyzed["company_id"], candidate["id"], candidate["task_id"],
        analyzed["user_id"], "hold", "やっぱり保留",
    )
    assert repos.decision_summary(analyzed["run_id"]) == {"hold": 1}
    stored = repos.get_candidate(candidate["id"])
    assert stored["decision"] == "hold"
    assert stored["decision_note"] == "やっぱり保留"

    repos.clear_decision(candidate["id"])
    assert repos.decision_summary(analyzed["run_id"]) == {}


def test_timestamps_are_normalized_to_utc(repos, org):
    session = repos.start_session(
        org["company_id"], org["user_id"], org["device_id"], "2025-05-01T09:00:00+09:00", "0.1.0"
    )
    stored = repos.get_session(session)
    assert stored["started_at"] == "2025-05-01T00:00:00+00:00"
    assert to_utc_iso("2025-05-01T09:00:00+09:00") == "2025-05-01T00:00:00+00:00"


def test_audit_log_records_actions(repos, org):
    repos.audit(f"user:{org['user_id']}", "consent.changed", org["company_id"],
                "scope", "browser_usage", {"enabled": False})
    logs = repos.list_audit(org["company_id"])
    assert logs[0]["action"] == "consent.changed"
    assert "browser_usage" in logs[0]["target_id"]
