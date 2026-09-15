"""登録制データモデルのリポジトリ。

名前による get-or-create（ensure_*）を基本にする。CSV/JSON 取り込みは
同じデータを何度流しても増殖してはいけないため。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Sequence

from ..core.models import (
    Company, Metric, Process, ProcessIssue, ProcessStep, Tool, VALID_SOURCES,
)
from .db import new_id, utcnow


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def _one(cur: sqlite3.Cursor) -> dict[str, Any] | None:
    row = cur.fetchone()
    return dict(row) if row else None


class RegistryRepo:
    """会社・部署・担当者・ツール・業務の登録。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ============================================================== 業種
    def ensure_industry(self, name: str, note: str | None = None) -> str:
        name = (name or "").strip()
        if not name:
            raise ValueError("業種名が空です")
        found = _one(self.conn.execute("SELECT id FROM industries WHERE name=?", (name,)))
        if found:
            return found["id"]
        iid = new_id()
        self.conn.execute(
            "INSERT INTO industries (id, name, note, created_at) VALUES (?,?,?,?)",
            (iid, name, note, utcnow()),
        )
        return iid

    def list_industries(self) -> list[dict]:
        return _rows(self.conn.execute("SELECT * FROM industries ORDER BY name"))

    # ============================================================== 会社
    def upsert_company(
        self,
        name: str,
        industry: str | None = None,
        employee_count: int | None = None,
        hourly_cost_jpy: int | None = None,
        note: str | None = None,
    ) -> str:
        """None の項目は既存値を残す。同じCSVを再投入しても値が壊れないように。"""
        name = (name or "").strip()
        if not name:
            raise ValueError("会社名が空です")
        industry_id = self.ensure_industry(industry) if industry else None
        found = _one(self.conn.execute("SELECT id FROM companies WHERE name=?", (name,)))
        if found:
            self.conn.execute(
                "UPDATE companies SET industry_id=COALESCE(?, industry_id),"
                " employee_count=COALESCE(?, employee_count),"
                " hourly_cost_jpy=COALESCE(?, hourly_cost_jpy),"
                " note=COALESCE(?, note) WHERE id=?",
                (industry_id, employee_count, hourly_cost_jpy, note, found["id"]),
            )
            return found["id"]
        cid = new_id()
        self.conn.execute(
            "INSERT INTO companies (id, name, hourly_cost_jpy, created_at,"
            " industry_id, employee_count, note) VALUES (?,?,?,?,?,?,?)",
            (cid, name, hourly_cost_jpy or 3500, utcnow(), industry_id,
             employee_count, note),
        )
        return cid

    def get_company(self, company_id: str) -> Company | None:
        row = _one(
            self.conn.execute(
                "SELECT c.*, i.name AS industry_name FROM companies c"
                " LEFT JOIN industries i ON i.id = c.industry_id WHERE c.id=?",
                (company_id,),
            )
        )
        if not row:
            return None
        return Company(
            id=row["id"], name=row["name"], industry_id=row.get("industry_id"),
            industry_name=row.get("industry_name"),
            employee_count=row.get("employee_count"),
            hourly_cost_jpy=int(row.get("hourly_cost_jpy") or 3500),
            note=row.get("note"),
        )

    def list_companies(self) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT c.*, i.name AS industry_name,"
                " (SELECT COUNT(*) FROM processes p WHERE p.company_id=c.id) AS process_count"
                " FROM companies c LEFT JOIN industries i ON i.id = c.industry_id"
                " ORDER BY c.created_at"
            )
        )

    # ============================================================== 部署
    def ensure_department(self, company_id: str, name: str, note: str | None = None) -> str:
        name = (name or "").strip()
        if not name:
            raise ValueError("部署名が空です")
        found = _one(
            self.conn.execute(
                "SELECT id FROM departments WHERE company_id=? AND name=?", (company_id, name)
            )
        )
        if found:
            return found["id"]
        did = new_id()
        self.conn.execute(
            "INSERT INTO departments (id, company_id, name, note, created_at)"
            " VALUES (?,?,?,?,?)",
            (did, company_id, name, note, utcnow()),
        )
        return did

    def list_departments(self, company_id: str) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT * FROM departments WHERE company_id=? ORDER BY name", (company_id,)
            )
        )

    # =========================================================== 担当者
    def ensure_staff(
        self, company_id: str, name: str, department: str | None = None,
        job_title: str | None = None, email: str | None = None,
        hourly_cost_jpy: int | None = None,
    ) -> str:
        name = (name or "").strip()
        if not name:
            raise ValueError("担当者名が空です")
        department_id = self.ensure_department(company_id, department) if department else None
        found = _one(
            self.conn.execute(
                "SELECT id FROM staff WHERE company_id=? AND name=?"
                " AND (department_id IS ? OR department_id=?)",
                (company_id, name, department_id, department_id),
            )
        )
        if found:
            return found["id"]
        sid = new_id()
        self.conn.execute(
            "INSERT INTO staff (id, company_id, department_id, name, job_title, email,"
            " hourly_cost_jpy, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (sid, company_id, department_id, name, job_title, email, hourly_cost_jpy, utcnow()),
        )
        return sid

    def list_staff(self, company_id: str) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT s.*, d.name AS department_name FROM staff s"
                " LEFT JOIN departments d ON d.id = s.department_id"
                " WHERE s.company_id=? ORDER BY s.name",
                (company_id,),
            )
        )

    # ============================================================ 使用PC
    def ensure_workplace(
        self, company_id: str, name: str, os_name: str | None = None, note: str | None = None
    ) -> str:
        found = _one(
            self.conn.execute(
                "SELECT id FROM workplaces WHERE company_id=? AND name=?", (company_id, name)
            )
        )
        if found:
            return found["id"]
        wid = new_id()
        self.conn.execute(
            "INSERT INTO workplaces (id, company_id, name, os, note, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (wid, company_id, name, os_name, note, utcnow()),
        )
        return wid

    def list_workplaces(self, company_id: str) -> list[dict]:
        return _rows(
            self.conn.execute(
                "SELECT * FROM workplaces WHERE company_id=? ORDER BY name", (company_id,)
            )
        )

    # ======================================== 使用ソフト / Webサービス
    def ensure_tool(
        self, company_id: str, name: str, kind: str | None = None,
        vendor: str | None = None, has_api: int = 2, has_csv_io: int = 2,
        url_domain: str | None = None, note: str | None = None,
    ) -> str:
        """kind を渡さなければ既存の種別を保つ。

        業務側からツールを参照するときは種別まで書かないことが多い。
        そこで既定値を書き戻すと、マスタで登録した種別（Webサービス等）が
        「インストール型ソフト」に潰れてしまう。
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("ツール名が空です")
        found = _one(
            self.conn.execute(
                "SELECT * FROM tools WHERE company_id=? AND name=?", (company_id, name)
            )
        )
        if found:
            # 後から API 有無が分かることがあるので、分かった情報だけ更新する
            self.conn.execute(
                "UPDATE tools SET kind=COALESCE(?, kind), vendor=COALESCE(?, vendor),"
                " has_api=CASE WHEN ?=2 THEN has_api ELSE ? END,"
                " has_csv_io=CASE WHEN ?=2 THEN has_csv_io ELSE ? END,"
                " url_domain=COALESCE(?, url_domain), note=COALESCE(?, note) WHERE id=?",
                (kind, vendor, has_api, has_api, has_csv_io, has_csv_io,
                 url_domain, note, found["id"]),
            )
            return found["id"]
        tid = new_id()
        self.conn.execute(
            "INSERT INTO tools (id, company_id, name, kind, vendor, has_api, has_csv_io,"
            " url_domain, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, company_id, name, kind or "software", vendor, has_api, has_csv_io,
             url_domain, note, utcnow()),
        )
        return tid

    def list_tools(self, company_id: str) -> list[Tool]:
        rows = _rows(
            self.conn.execute(
                "SELECT * FROM tools WHERE company_id=? ORDER BY name", (company_id,)
            )
        )
        return [
            Tool(id=r["id"], name=r["name"], kind=r["kind"], vendor=r.get("vendor"),
                 has_api=int(r["has_api"]), has_csv_io=int(r["has_csv_io"]),
                 url_domain=r.get("url_domain"), note=r.get("note"))
            for r in rows
        ]

    # ============================================================== 業務
    def upsert_process(
        self,
        company_id: str,
        name: str,
        summary: str = "",
        category: str = "",
        department: str | None = None,
        owner: str | None = None,
        has_data_entry: bool = False,
        has_transcription: bool = False,
        has_judgment: bool = False,
        judgment_note: str = "",
        is_person_dependent: bool = False,
        security_level: str = "normal",
        origin: str = "manual",
        origin_note: str = "",
    ) -> str:
        name = (name or "").strip()
        if not name:
            raise ValueError("業務名が空です")
        department_id = self.ensure_department(company_id, department) if department else None
        owner_id = (
            self.ensure_staff(company_id, owner, department) if owner else None
        )
        now = utcnow()
        found = _one(
            self.conn.execute(
                "SELECT id FROM processes WHERE company_id=? AND name=?", (company_id, name)
            )
        )
        values = (
            department_id, owner_id, summary, category,
            1 if has_data_entry else 0, 1 if has_transcription else 0,
            1 if has_judgment else 0, judgment_note,
            1 if is_person_dependent else 0, security_level, origin, origin_note, now,
        )
        if found:
            self.conn.execute(
                "UPDATE processes SET department_id=COALESCE(?, department_id),"
                " owner_staff_id=COALESCE(?, owner_staff_id), summary=?, category=?,"
                " has_data_entry=?, has_transcription=?, has_judgment=?, judgment_note=?,"
                " is_person_dependent=?, security_level=?, origin=?, origin_note=?,"
                " updated_at=? WHERE id=?",
                (*values, found["id"]),
            )
            return found["id"]
        pid = new_id()
        self.conn.execute(
            "INSERT INTO processes (id, company_id, name, department_id, owner_staff_id,"
            " summary, category, has_data_entry, has_transcription, has_judgment,"
            " judgment_note, is_person_dependent, security_level, origin, origin_note,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, company_id, name, *values[:-1], now, now),
        )
        return pid

    # -------------------------------------------------------- 数値の登録
    def set_metric(
        self, process_id: str, key: str, value: float, source: str,
        unit: str = "", evidence: str = "",
    ) -> None:
        """出所つきで数値を登録する。出所が無いものは登録できない。"""
        metric = Metric(key=key, value=float(value), source=source, unit=unit,
                        evidence=evidence)
        self.conn.execute(
            "INSERT INTO process_metrics (id, process_id, metric_key, value_num, unit,"
            " source, evidence, recorded_at) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(process_id, metric_key) DO UPDATE SET"
            " value_num=excluded.value_num, unit=excluded.unit, source=excluded.source,"
            " evidence=excluded.evidence, recorded_at=excluded.recorded_at",
            (new_id(), process_id, metric.key, metric.value, metric.unit,
             metric.source, metric.evidence, utcnow()),
        )

    def set_process_tools(self, process_id: str, entries: Sequence[dict]) -> None:
        """業務が使うツールを貼り直す。"""
        self.conn.execute("DELETE FROM process_tools WHERE process_id=?", (process_id,))
        for entry in entries:
            self.conn.execute(
                "INSERT OR IGNORE INTO process_tools (id, process_id, tool_id, role)"
                " VALUES (?,?,?,?)",
                (new_id(), process_id, entry["tool_id"], entry.get("role")),
            )

    def set_process_steps(self, process_id: str, steps: Sequence[dict]) -> None:
        self.conn.execute("DELETE FROM process_steps WHERE process_id=?", (process_id,))
        for i, step in enumerate(steps, start=1):
            self.conn.execute(
                "INSERT INTO process_steps (id, process_id, seq, action, tool_id,"
                " input_info, output_info, note) VALUES (?,?,?,?,?,?,?,?)",
                (new_id(), process_id, step.get("seq", i), step.get("action", ""),
                 step.get("tool_id"), step.get("input_info", ""),
                 step.get("output_info", ""), step.get("note", "")),
            )

    def set_process_issues(self, process_id: str, issues: Sequence[dict]) -> None:
        self.conn.execute("DELETE FROM process_issues WHERE process_id=?", (process_id,))
        for issue in issues:
            if not issue.get("description"):
                continue
            self.conn.execute(
                "INSERT INTO process_issues (id, process_id, kind, description, created_at)"
                " VALUES (?,?,?,?,?)",
                (new_id(), process_id, issue.get("kind", "problem"),
                 issue["description"], utcnow()),
            )

    # ---------------------------------------------------------- 取り出し
    def get_process(self, process_id: str) -> Process | None:
        row = _one(
            self.conn.execute(
                "SELECT p.*, d.name AS department_name, s.name AS owner_name"
                " FROM processes p"
                " LEFT JOIN departments d ON d.id = p.department_id"
                " LEFT JOIN staff s ON s.id = p.owner_staff_id"
                " WHERE p.id=?",
                (process_id,),
            )
        )
        if not row:
            return None
        return self._build_process(row, with_details=True)

    def list_processes(self, company_id: str) -> list[Process]:
        rows = _rows(
            self.conn.execute(
                "SELECT p.*, d.name AS department_name, s.name AS owner_name"
                " FROM processes p"
                " LEFT JOIN departments d ON d.id = p.department_id"
                " LEFT JOIN staff s ON s.id = p.owner_staff_id"
                " WHERE p.company_id=? ORDER BY p.created_at",
                (company_id,),
            )
        )
        return [self._build_process(r, with_details=False) for r in rows]

    def _build_process(self, row: dict, with_details: bool) -> Process:
        process = Process(
            id=row["id"], company_id=row["company_id"], name=row["name"],
            summary=row.get("summary") or "", category=row.get("category") or "",
            department_id=row.get("department_id"),
            department_name=row.get("department_name"),
            owner_staff_id=row.get("owner_staff_id"), owner_name=row.get("owner_name"),
            has_data_entry=bool(row.get("has_data_entry")),
            has_transcription=bool(row.get("has_transcription")),
            has_judgment=bool(row.get("has_judgment")),
            judgment_note=row.get("judgment_note") or "",
            is_person_dependent=bool(row.get("is_person_dependent")),
            security_level=row.get("security_level") or "normal",
            origin=row.get("origin") or "manual",
            origin_note=row.get("origin_note") or "",
        )
        for m in _rows(
            self.conn.execute(
                "SELECT * FROM process_metrics WHERE process_id=?", (process.id,)
            )
        ):
            process.metrics[m["metric_key"]] = Metric(
                key=m["metric_key"], value=float(m["value_num"]), source=m["source"],
                unit=m.get("unit") or "", evidence=m.get("evidence") or "",
            )
        process.tools = [
            Tool(id=t["id"], name=t["name"], kind=t["kind"], vendor=t.get("vendor"),
                 has_api=int(t["has_api"]), has_csv_io=int(t["has_csv_io"]),
                 url_domain=t.get("url_domain"))
            for t in _rows(
                self.conn.execute(
                    "SELECT t.* FROM process_tools pt JOIN tools t ON t.id = pt.tool_id"
                    " WHERE pt.process_id=? ORDER BY t.name",
                    (process.id,),
                )
            )
        ]
        if with_details:
            process.steps = [
                ProcessStep(
                    seq=s["seq"], action=s["action"], tool_id=s.get("tool_id"),
                    tool_name=s.get("tool_name"), input_info=s.get("input_info") or "",
                    output_info=s.get("output_info") or "", note=s.get("note") or "",
                )
                for s in _rows(
                    self.conn.execute(
                        "SELECT ps.*, t.name AS tool_name FROM process_steps ps"
                        " LEFT JOIN tools t ON t.id = ps.tool_id"
                        " WHERE ps.process_id=? ORDER BY ps.seq",
                        (process.id,),
                    )
                )
            ]
            process.issues = [
                ProcessIssue(kind=i["kind"], description=i["description"])
                for i in _rows(
                    self.conn.execute(
                        "SELECT * FROM process_issues WHERE process_id=? ORDER BY created_at",
                        (process.id,),
                    )
                )
            ]
        return process

    def delete_process(self, process_id: str) -> None:
        self.conn.execute("DELETE FROM processes WHERE id=?", (process_id,))

    # ===================================================== 取り込み記録
    def record_import(
        self, company_id: str, kind: str, filename: str | None,
        created: int, updated: int, skipped: int, errors: Iterable[str],
    ) -> str:
        rid = new_id()
        self.conn.execute(
            "INSERT INTO import_runs (id, company_id, kind, filename, created, updated,"
            " skipped, errors_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, company_id, kind, filename, created, updated, skipped,
             json.dumps(list(errors), ensure_ascii=False), utcnow()),
        )
        return rid

    def list_imports(self, company_id: str, limit: int = 20) -> list[dict]:
        rows = _rows(
            self.conn.execute(
                "SELECT * FROM import_runs WHERE company_id=?"
                " ORDER BY created_at DESC LIMIT ?",
                (company_id, limit),
            )
        )
        for r in rows:
            r["errors"] = json.loads(r["errors_json"] or "[]")
        return rows
