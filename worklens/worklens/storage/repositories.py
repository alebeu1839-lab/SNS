"""エンティティごとのリポジトリ。上位層は SQL を直接書かない。"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Sequence

from .db import new_id, to_utc_iso, utcnow

Row = sqlite3.Row


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def _opt_utc(ts: str | None) -> str | None:
    return to_utc_iso(ts) if ts else None


def _one(cur: sqlite3.Cursor) -> dict[str, Any] | None:
    r = cur.fetchone()
    return dict(r) if r else None


class Repositories:
    """全リポジトリのファサード。1コネクション = 1インスタンス。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ================================================================ 企業
    def create_company(self, name: str, hourly_cost_jpy: int = 3500) -> str:
        cid = new_id()
        self.conn.execute(
            "INSERT INTO companies (id, name, hourly_cost_jpy, created_at) VALUES (?,?,?,?)",
            (cid, name, hourly_cost_jpy, utcnow()),
        )
        return cid

    def get_company(self, company_id: str) -> dict | None:
        return _one(self.conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)))

    def list_companies(self) -> list[dict]:
        return _rows(self.conn.execute("SELECT * FROM companies ORDER BY created_at"))

    def update_company_cost(self, company_id: str, hourly_cost_jpy: int) -> None:
        self.conn.execute(
            "UPDATE companies SET hourly_cost_jpy=? WHERE id=?", (hourly_cost_jpy, company_id)
        )

    # ========================================================== ユーザー
    def create_user(
        self,
        company_id: str,
        email: str,
        display_name: str,
        department: str | None = None,
        role: str = "member",
    ) -> str:
        uid = new_id()
        self.conn.execute(
            "INSERT INTO users (id, company_id, email, display_name, department, role, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (uid, company_id, email, display_name, department, role, utcnow()),
        )
        return uid

    def get_user(self, user_id: str) -> dict | None:
        return _one(self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)))

    def find_user(self, company_id: str, email: str) -> dict | None:
        return _one(
            self.conn.execute(
                "SELECT * FROM users WHERE company_id=? AND email=?", (company_id, email)
            )
        )

    def list_users(self, company_id: str) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT * FROM users WHERE company_id=? ORDER BY display_name", (company_id,)
            )
        )

    # ================================================================= PC
    def register_device(
        self,
        company_id: str,
        user_id: str,
        hostname: str,
        os_name: str,
        agent_version: str,
        os_version: str | None = None,
        timezone_name: str = "Asia/Tokyo",
    ) -> str:
        existing = _one(
            self.conn.execute(
                "SELECT * FROM devices WHERE user_id=? AND hostname=?", (user_id, hostname)
            )
        )
        if existing:
            self.conn.execute(
                "UPDATE devices SET agent_version=?, last_seen_at=? WHERE id=?",
                (agent_version, utcnow(), existing["id"]),
            )
            return existing["id"]
        did = new_id()
        self.conn.execute(
            "INSERT INTO devices (id, company_id, user_id, hostname, os, os_version,"
            " agent_version, timezone, registered_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                did, company_id, user_id, hostname, os_name, os_version,
                agent_version, timezone_name, utcnow(), utcnow(),
            ),
        )
        return did

    def get_device(self, device_id: str) -> dict | None:
        return _one(self.conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)))

    def list_devices(self, user_id: str) -> list[dict]:
        return _rows(
            self.conn.execute("SELECT * FROM devices WHERE user_id=? ORDER BY hostname", (user_id,))
        )

    def touch_device(self, device_id: str) -> None:
        self.conn.execute("UPDATE devices SET last_seen_at=? WHERE id=?", (utcnow(), device_id))

    # ==================================================== 同意 / 収集設定
    def set_consent(
        self,
        company_id: str,
        user_id: str,
        device_id: str | None,
        scope_key: str,
        enabled: bool,
    ) -> None:
        now = utcnow()
        self.conn.execute(
            "INSERT INTO consents (id, company_id, user_id, device_id, scope_key, enabled,"
            " granted_at, revoked_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(user_id, device_id, scope_key) DO UPDATE SET"
            "   enabled=excluded.enabled,"
            "   granted_at=CASE WHEN excluded.enabled=1 THEN excluded.granted_at ELSE consents.granted_at END,"
            "   revoked_at=CASE WHEN excluded.enabled=0 THEN excluded.updated_at ELSE NULL END,"
            "   updated_at=excluded.updated_at",
            (
                new_id(), company_id, user_id, device_id, scope_key, 1 if enabled else 0,
                now if enabled else None, None if enabled else now, now,
            ),
        )

    def consent_map(self, user_id: str, device_id: str | None) -> dict[str, bool]:
        rows = _rows(
            self.conn.execute(
                "SELECT scope_key, enabled FROM consents WHERE user_id=?"
                " AND (device_id=? OR device_id IS NULL)",
                (user_id, device_id),
            )
        )
        return {r["scope_key"]: bool(r["enabled"]) for r in rows}

    def list_consents(self, user_id: str) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT * FROM consents WHERE user_id=? ORDER BY scope_key", (user_id,)
            )
        )

    # ========================================================= セッション
    def start_session(
        self, company_id: str, user_id: str, device_id: str, started_at: str, agent_version: str
    ) -> str:
        sid = new_id()
        self.conn.execute(
            "INSERT INTO sessions (id, company_id, user_id, device_id, started_at,"
            " agent_version, created_at) VALUES (?,?,?,?,?,?,?)",
            (sid, company_id, user_id, device_id, to_utc_iso(started_at), agent_version, utcnow()),
        )
        return sid

    def end_session(self, session_id: str, ended_at: str, paused_sec: int = 0) -> None:
        self.conn.execute(
            "UPDATE sessions SET ended_at=?, paused_sec=? WHERE id=?",
            (to_utc_iso(ended_at), paused_sec, session_id),
        )

    def get_session(self, session_id: str) -> dict | None:
        return _one(self.conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)))

    def list_sessions(
        self, user_id: str, period_start: str | None = None, period_end: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM sessions WHERE user_id=?"
        args: list[Any] = [user_id]
        if period_start:
            sql += " AND started_at >= ?"
            args.append(period_start)
        if period_end:
            sql += " AND started_at <= ?"
            args.append(period_end)
        sql += " ORDER BY started_at"
        return _rows(self.conn.execute(sql, args))

    def open_session_for_device(self, device_id: str) -> dict | None:
        return _one(
            self.conn.execute(
                "SELECT * FROM sessions WHERE device_id=? AND ended_at IS NULL"
                " ORDER BY started_at DESC LIMIT 1",
                (device_id,),
            )
        )

    # ======================================================= 操作イベント
    def insert_events(self, events: Sequence[dict]) -> int:
        if not events:
            return 0
        now = utcnow()
        self.conn.executemany(
            "INSERT INTO events (id, company_id, session_id, ts, event_type, scope_key,"
            " app_name, app_category, window_title, url_domain, url_path_shape, file_ext,"
            " file_op, detail, redacted, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    e.get("id") or new_id(), e["company_id"], e["session_id"], to_utc_iso(e["ts"]),
                    e["event_type"], e["scope_key"], e.get("app_name"), e.get("app_category"),
                    e.get("window_title"), e.get("url_domain"), e.get("url_path_shape"),
                    e.get("file_ext"), e.get("file_op"),
                    json.dumps(e["detail"], ensure_ascii=False) if isinstance(e.get("detail"), (dict, list)) else e.get("detail"),
                    1 if e.get("redacted") else 0, now,
                )
                for e in events
            ],
        )
        return len(events)

    def list_events(
        self, session_id: str | None = None, company_id: str | None = None,
        period_start: str | None = None, period_end: str | None = None, limit: int | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM events WHERE 1=1"
        args: list[Any] = []
        if session_id:
            sql += " AND session_id=?"
            args.append(session_id)
        if company_id:
            sql += " AND company_id=?"
            args.append(company_id)
        if period_start:
            sql += " AND ts >= ?"
            args.append(period_start)
        if period_end:
            sql += " AND ts <= ?"
            args.append(period_end)
        sql += " ORDER BY ts"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return _rows(self.conn.execute(sql, args))

    def count_events(self, company_id: str) -> int:
        cur = self.conn.execute("SELECT COUNT(*) c FROM events WHERE company_id=?", (company_id,))
        return int(cur.fetchone()["c"])

    def event_type_breakdown(self, company_id: str) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT event_type, scope_key, COUNT(*) AS count,"
                " SUM(redacted) AS redacted_count FROM events WHERE company_id=?"
                " GROUP BY event_type, scope_key ORDER BY count DESC",
                (company_id,),
            )
        )

    # ========================================================= アプリ使用
    def insert_app_usage(self, rows: Sequence[dict]) -> int:
        if not rows:
            return 0
        now = utcnow()
        self.conn.executemany(
            "INSERT INTO app_usage (id, company_id, session_id, app_name, app_category,"
            " started_at, ended_at, duration_sec, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    new_id(), r["company_id"], r["session_id"], r["app_name"],
                    r.get("app_category"), to_utc_iso(r["started_at"]), to_utc_iso(r["ended_at"]),
                    int(r["duration_sec"]), now,
                )
                for r in rows
            ],
        )
        return len(rows)

    def app_usage_totals(self, company_id: str, period_start: str, period_end: str) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT app_name, app_category, SUM(duration_sec) AS total_sec,"
                " COUNT(*) AS segments FROM app_usage"
                " WHERE company_id=? AND started_at >= ? AND started_at <= ?"
                " GROUP BY app_name, app_category ORDER BY total_sec DESC",
                (company_id, period_start, period_end),
            )
        )

    # ========================================================== 分析ラン
    def start_run(
        self, company_id: str, user_id: str | None, period_start: str, period_end: str, engine: str
    ) -> str:
        rid = new_id()
        self.conn.execute(
            "INSERT INTO analysis_runs (id, company_id, user_id, period_start, period_end,"
            " engine, status, started_at) VALUES (?,?,?,?,?,?,'running',?)",
            (rid, company_id, user_id, period_start, period_end, engine, utcnow()),
        )
        return rid

    def finish_run(self, run_id: str, stats: dict) -> None:
        self.conn.execute(
            "UPDATE analysis_runs SET status='succeeded', stats_json=?, finished_at=? WHERE id=?",
            (json.dumps(stats, ensure_ascii=False), utcnow(), run_id),
        )

    def fail_run(self, run_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE analysis_runs SET status='failed', error=?, finished_at=? WHERE id=?",
            (error[:2000], utcnow(), run_id),
        )

    def get_run(self, run_id: str) -> dict | None:
        return _one(self.conn.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)))

    def latest_run(self, company_id: str) -> dict | None:
        return _one(
            self.conn.execute(
                "SELECT * FROM analysis_runs WHERE company_id=? AND status='succeeded'"
                " ORDER BY started_at DESC LIMIT 1",
                (company_id,),
            )
        )

    def list_runs(self, company_id: str) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT * FROM analysis_runs WHERE company_id=? ORDER BY started_at DESC",
                (company_id,),
            )
        )

    # =============================================================== 業務
    def create_task(self, task: dict, steps: Iterable[dict], occurrences: Iterable[dict]) -> str:
        tid = task.get("id") or new_id()
        self.conn.execute(
            "INSERT INTO tasks (id, company_id, user_id, run_id, name, summary, category,"
            " apps_json, occurrence_count, avg_duration_sec, total_duration_sec, per_day_count,"
            " is_repetitive, repetition_score, confidence, inference_source, first_seen_at,"
            " last_seen_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid, task["company_id"], task["user_id"], task["run_id"], task["name"],
                task["summary"], task["category"],
                json.dumps(task.get("apps", []), ensure_ascii=False),
                int(task.get("occurrence_count", 0)), int(task.get("avg_duration_sec", 0)),
                int(task.get("total_duration_sec", 0)), float(task.get("per_day_count", 0.0)),
                1 if task.get("is_repetitive") else 0, float(task.get("repetition_score", 0.0)),
                float(task.get("confidence", 0.0)), task.get("inference_source", "rule"),
                _opt_utc(task.get("first_seen_at")), _opt_utc(task.get("last_seen_at")),
                utcnow(),
            ),
        )
        for i, s in enumerate(steps, start=1):
            self.conn.execute(
                "INSERT INTO task_steps (id, task_id, seq, action, app_name, detail)"
                " VALUES (?,?,?,?,?,?)",
                (new_id(), tid, i, s.get("action", ""), s.get("app_name"), s.get("detail", "")),
            )
        for o in occurrences:
            self.conn.execute(
                "INSERT INTO task_occurrences (id, task_id, session_id, started_at, ended_at,"
                " duration_sec, evidence) VALUES (?,?,?,?,?,?,?)",
                (
                    new_id(), tid, o["session_id"], to_utc_iso(o["started_at"]),
                    to_utc_iso(o["ended_at"]),
                    int(o["duration_sec"]),
                    json.dumps(o.get("evidence", []), ensure_ascii=False),
                ),
            )
        return tid

    def get_task(self, task_id: str) -> dict | None:
        t = _one(self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)))
        if not t:
            return None
        t["apps"] = json.loads(t["apps_json"] or "[]")
        t["steps"] = _rows(
            self.conn.execute("SELECT * FROM task_steps WHERE task_id=? ORDER BY seq", (task_id,))
        )
        t["occurrences"] = _rows(
            self.conn.execute(
                "SELECT * FROM task_occurrences WHERE task_id=? ORDER BY started_at", (task_id,)
            )
        )
        return t

    def list_tasks(self, run_id: str) -> list[dict]:
        tasks = _rows(
            self.conn.execute(
                "SELECT * FROM tasks WHERE run_id=? ORDER BY total_duration_sec DESC", (run_id,)
            )
        )
        for t in tasks:
            t["apps"] = json.loads(t["apps_json"] or "[]")
        return tasks

    # ======================================================= 自動化候補
    def create_candidates(self, candidates: Sequence[dict]) -> list[str]:
        ids: list[str] = []
        now = utcnow()
        for c in candidates:
            cid = c.get("id") or new_id()
            ids.append(cid)
            self.conn.execute(
                "INSERT INTO automation_candidates (id, company_id, task_id, run_id, rank,"
                " feasibility, method, method_detail, tools_json, difficulty, difficulty_score,"
                " risk_level, risk_notes, human_judgment, human_judgment_ratio,"
                " frequency_per_month, est_minutes_per_run, est_current_minutes_month,"
                " est_saved_minutes_month, est_saved_cost_month_jpy, priority_score, rationale,"
                " step2_spec_json, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cid, c["company_id"], c["task_id"], c["run_id"], int(c["rank"]),
                    float(c["feasibility"]), c["method"], c["method_detail"],
                    json.dumps(c.get("tools", []), ensure_ascii=False), c["difficulty"],
                    float(c["difficulty_score"]), c["risk_level"], c["risk_notes"],
                    c["human_judgment"], float(c["human_judgment_ratio"]),
                    float(c["frequency_per_month"]), float(c["est_minutes_per_run"]),
                    float(c["est_current_minutes_month"]), float(c["est_saved_minutes_month"]),
                    int(c["est_saved_cost_month_jpy"]), float(c["priority_score"]),
                    c["rationale"],
                    json.dumps(c.get("step2_spec", {}), ensure_ascii=False), now,
                ),
            )
        return ids

    def list_candidates(self, run_id: str) -> list[dict]:
        rows = _rows(
            self.conn.execute(
                "SELECT c.*, t.name AS task_name, t.summary AS task_summary,"
                " t.category AS task_category, t.apps_json AS task_apps_json,"
                " t.is_repetitive AS task_is_repetitive,"
                " d.decision AS decision, d.note AS decision_note, d.decided_at AS decided_at"
                " FROM automation_candidates c"
                " JOIN tasks t ON t.id = c.task_id"
                " LEFT JOIN candidate_decisions d ON d.candidate_id = c.id"
                " WHERE c.run_id=? ORDER BY c.rank",
                (run_id,),
            )
        )
        for r in rows:
            r["tools"] = json.loads(r["tools_json"] or "[]")
            r["task_apps"] = json.loads(r["task_apps_json"] or "[]")
        return rows

    def get_candidate(self, candidate_id: str) -> dict | None:
        r = _one(
            self.conn.execute(
                "SELECT c.*, t.name AS task_name, t.summary AS task_summary,"
                " t.category AS task_category, t.apps_json AS task_apps_json,"
                " t.occurrence_count AS task_occurrence_count,"
                " t.repetition_score AS task_repetition_score,"
                " t.confidence AS task_confidence,"
                " t.inference_source AS task_inference_source,"
                " d.decision AS decision, d.note AS decision_note, d.decided_at AS decided_at"
                " FROM automation_candidates c"
                " JOIN tasks t ON t.id = c.task_id"
                " LEFT JOIN candidate_decisions d ON d.candidate_id = c.id"
                " WHERE c.id=?",
                (candidate_id,),
            )
        )
        if not r:
            return None
        r["tools"] = json.loads(r["tools_json"] or "[]")
        r["task_apps"] = json.loads(r["task_apps_json"] or "[]")
        r["step2_spec"] = json.loads(r["step2_spec_json"] or "{}")
        r["task"] = self.get_task(r["task_id"])
        return r

    # ============================================== ユーザーによる選択結果
    def set_decision(
        self, company_id: str, candidate_id: str, task_id: str, user_id: str,
        decision: str, note: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO candidate_decisions (id, company_id, candidate_id, task_id,"
            " decided_by, decision, note, decided_at) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(candidate_id) DO UPDATE SET decision=excluded.decision,"
            " note=excluded.note, decided_by=excluded.decided_by, decided_at=excluded.decided_at",
            (new_id(), company_id, candidate_id, task_id, user_id, decision, note, utcnow()),
        )

    def clear_decision(self, candidate_id: str) -> None:
        self.conn.execute("DELETE FROM candidate_decisions WHERE candidate_id=?", (candidate_id,))

    def decision_summary(self, run_id: str) -> dict[str, int]:
        rows = _rows(
            self.conn.execute(
                "SELECT d.decision, COUNT(*) AS c FROM candidate_decisions d"
                " JOIN automation_candidates a ON a.id = d.candidate_id"
                " WHERE a.run_id=? GROUP BY d.decision",
                (run_id,),
            )
        )
        return {r["decision"]: r["c"] for r in rows}

    # ========================================================== 監査ログ
    def audit(
        self, actor: str, action: str, company_id: str | None = None,
        target_type: str | None = None, target_id: str | None = None, detail: Any = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO audit_logs (id, company_id, actor, action, target_type, target_id,"
            " detail, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                new_id(), company_id, actor, action, target_type, target_id,
                json.dumps(detail, ensure_ascii=False) if detail is not None else None,
                utcnow(),
            ),
        )

    def list_audit(self, company_id: str, limit: int = 200) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT * FROM audit_logs WHERE company_id=? ORDER BY created_at DESC LIMIT ?",
                (company_id, limit),
            )
        )

    # ============================================== 除外・マスクの統計
    def bump_redaction(
        self, company_id: str, session_id: str | None, reason: str,
        scope_key: str | None, count: int = 1,
    ) -> None:
        self.conn.execute(
            "INSERT INTO redaction_stats (id, company_id, session_id, reason, scope_key,"
            " count, updated_at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(session_id, reason, scope_key) DO UPDATE SET"
            " count = redaction_stats.count + excluded.count, updated_at = excluded.updated_at",
            (new_id(), company_id, session_id, reason, scope_key, count, utcnow()),
        )

    def redaction_summary(self, company_id: str) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT reason, scope_key, SUM(count) AS count FROM redaction_stats"
                " WHERE company_id=? GROUP BY reason, scope_key ORDER BY count DESC",
                (company_id,),
            )
        )

    # ============================== STEP2: 自動化の実行記録
    def record_execution(self, execution: dict) -> str:
        eid = execution.get("id") or new_id()
        self.conn.execute(
            "INSERT INTO automation_executions (id, company_id, candidate_id, task_id,"
            " recipe, mode, status, processed, succeeded, handoff, failed, saved_minutes,"
            " log_path, detail_json, error, started_at, finished_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid, execution["company_id"], execution["candidate_id"], execution["task_id"],
                execution["recipe"], execution["mode"], execution["status"],
                int(execution.get("processed", 0)), int(execution.get("succeeded", 0)),
                int(execution.get("handoff", 0)), int(execution.get("failed", 0)),
                float(execution.get("saved_minutes", 0.0)), execution.get("log_path"),
                json.dumps(execution.get("detail", {}), ensure_ascii=False),
                execution.get("error"),
                to_utc_iso(execution["started_at"]),
                _opt_utc(execution.get("finished_at")),
            ),
        )
        return eid

    def list_executions(self, candidate_id: str | None = None,
                        company_id: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM automation_executions WHERE 1=1"
        args: list[Any] = []
        if candidate_id:
            sql += " AND candidate_id=?"
            args.append(candidate_id)
        if company_id:
            sql += " AND company_id=?"
            args.append(company_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        args.append(limit)
        rows = _rows(self.conn.execute(sql, args))
        for r in rows:
            r["detail"] = json.loads(r["detail_json"] or "{}")
        return rows

    def execution_totals(self, company_id: str) -> dict[str, Any]:
        row = _one(
            self.conn.execute(
                "SELECT COUNT(*) AS runs, SUM(succeeded) AS succeeded, SUM(handoff) AS handoff,"
                " SUM(failed) AS failed, SUM(saved_minutes) AS saved_minutes"
                " FROM automation_executions WHERE company_id=? AND mode='live'",
                (company_id,),
            )
        )
        return {k: (v or 0) for k, v in (row or {}).items()}

    # ====================== STEP2: 人へ引き継いだ件
    def record_handoffs(
        self, company_id: str, execution_id: str, candidate_id: str,
        task_id: str, items: Sequence[dict],
    ) -> int:
        """自動処理しなかった件を未処理キューへ入れる。

        同じ対象が繰り返し引き継がれるので、未処理のものが既にあれば
        理由だけ更新して重複させない。
        """
        if not items:
            return 0
        now = utcnow()
        added = 0
        for item in items:
            existing = _one(
                self.conn.execute(
                    "SELECT id FROM automation_handoffs WHERE candidate_id=? AND item_key=?"
                    " AND status='open'",
                    (candidate_id, item["item_key"]),
                )
            )
            context = json.dumps(item.get("context", {}), ensure_ascii=False)
            if existing:
                self.conn.execute(
                    "UPDATE automation_handoffs SET reason=?, context_json=?,"
                    " execution_id=? WHERE id=?",
                    (item["reason"], context, execution_id, existing["id"]),
                )
                continue
            self.conn.execute(
                "INSERT INTO automation_handoffs (id, company_id, execution_id, candidate_id,"
                " task_id, item_key, reason, context_json, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,'open',?)",
                (
                    new_id(), company_id, execution_id, candidate_id, task_id,
                    item["item_key"], item["reason"], context, now,
                ),
            )
            added += 1
        return added

    def list_handoffs(
        self, company_id: str, status: str | None = "open",
        candidate_id: str | None = None, limit: int = 200,
    ) -> list[dict]:
        sql = (
            "SELECT h.*, t.name AS task_name, c.rank AS candidate_rank"
            " FROM automation_handoffs h"
            " JOIN tasks t ON t.id = h.task_id"
            " JOIN automation_candidates c ON c.id = h.candidate_id"
            " WHERE h.company_id=?"
        )
        args: list[Any] = [company_id]
        if status:
            sql += " AND h.status=?"
            args.append(status)
        if candidate_id:
            sql += " AND h.candidate_id=?"
            args.append(candidate_id)
        sql += " ORDER BY h.created_at DESC LIMIT ?"
        args.append(limit)
        rows = _rows(self.conn.execute(sql, args))
        for r in rows:
            r["context"] = json.loads(r["context_json"] or "{}")
        return rows

    def resolve_handoff(self, handoff_id: str, user_id: str, note: str | None = None) -> None:
        self.conn.execute(
            "UPDATE automation_handoffs SET status='resolved', resolved_by=?, resolved_at=?,"
            " note=? WHERE id=?",
            (user_id, utcnow(), note, handoff_id),
        )

    def handoff_counts(self, company_id: str) -> dict[str, int]:
        rows = _rows(
            self.conn.execute(
                "SELECT status, COUNT(*) AS c FROM automation_handoffs"
                " WHERE company_id=? GROUP BY status",
                (company_id,),
            )
        )
        return {r["status"]: r["c"] for r in rows}

    # ==================================================== データ削除機能
    def purge_user_data(self, user_id: str) -> dict[str, int]:
        """ユーザーの収集データと分析結果を消す（アカウント自体は残す）。"""
        counts: dict[str, int] = {}
        sess = [r["id"] for r in self.list_sessions(user_id)]
        if sess:
            marks = ",".join("?" * len(sess))
            counts["events"] = self.conn.execute(
                f"DELETE FROM events WHERE session_id IN ({marks})", sess
            ).rowcount
            counts["app_usage"] = self.conn.execute(
                f"DELETE FROM app_usage WHERE session_id IN ({marks})", sess
            ).rowcount
            counts["sessions"] = self.conn.execute(
                f"DELETE FROM sessions WHERE id IN ({marks})", sess
            ).rowcount
        counts["tasks"] = self.conn.execute(
            "DELETE FROM tasks WHERE user_id=?", (user_id,)
        ).rowcount
        counts["runs"] = self.conn.execute(
            "DELETE FROM analysis_runs WHERE user_id=?", (user_id,)
        ).rowcount
        return {k: max(v, 0) for k, v in counts.items()}

    def purge_company_data(self, company_id: str) -> dict[str, int]:
        counts = {}
        for table in (
            "events", "app_usage", "redaction_stats", "automation_handoffs",
            "automation_executions",
            "automation_candidates", "candidate_decisions", "tasks", "analysis_runs",
            "sessions",
        ):
            counts[table] = max(
                self.conn.execute(
                    f"DELETE FROM {table} WHERE company_id=?", (company_id,)
                ).rowcount,
                0,
            )
        return counts
