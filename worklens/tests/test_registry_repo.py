"""登録リポジトリの検証。取り込みの冪等性が主眼。"""
from __future__ import annotations

import pytest

from worklens.storage.db import init_db
from worklens.storage.registry_repo import RegistryRepo


@pytest.fixture()
def reg(home):
    return RegistryRepo(init_db(home / "worklens.db"))


def test_existing_tables_survive_the_new_schema(reg):
    """既存の観測系テーブルを壊していないこと。"""
    names = {r[0] for r in reg.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "tasks", "automation_candidates"} <= names
    assert {"processes", "process_metrics", "tools", "departments"} <= names


def test_company_columns_were_added_without_data_loss(reg):
    cid = reg.upsert_company("A社", industry="小売", employee_count=10, hourly_cost_jpy=3000)
    company = reg.get_company(cid)
    assert company.industry_name == "小売"
    assert company.employee_count == 10
    assert company.hourly_cost_jpy == 3000


def test_reimport_does_not_duplicate_or_reset_values(reg):
    cid = reg.upsert_company("A社", industry="小売", employee_count=10, hourly_cost_jpy=3000)
    # 2回目は一部だけ渡す（CSVの再投入を想定）
    again = reg.upsert_company("A社")
    assert again == cid
    company = reg.get_company(cid)
    assert company.hourly_cost_jpy == 3000, "渡されなかった値は保たれる"
    assert company.employee_count == 10
    assert len(reg.list_companies()) == 1


def test_masters_are_get_or_create(reg):
    cid = reg.upsert_company("A社")
    d1 = reg.ensure_department(cid, "業務課")
    d2 = reg.ensure_department(cid, "業務課")
    assert d1 == d2 and len(reg.list_departments(cid)) == 1

    s1 = reg.ensure_staff(cid, "山田", department="業務課")
    s2 = reg.ensure_staff(cid, "山田", department="業務課")
    assert s1 == s2 and len(reg.list_staff(cid)) == 1


def test_tool_kind_is_not_overwritten_by_a_bare_reference(reg):
    """業務からツールを参照しただけで、マスタの種別を潰さないこと。"""
    cid = reg.upsert_company("A社")
    reg.ensure_tool(cid, "掲載サイト", kind="web_service", has_api=0)
    reg.ensure_tool(cid, "掲載サイト")          # 種別を指定しない参照
    tool = next(t for t in reg.list_tools(cid) if t.name == "掲載サイト")
    assert tool.kind == "web_service"
    assert tool.has_api == 0


def test_api_flag_is_updated_once_it_becomes_known(reg):
    cid = reg.upsert_company("A社")
    reg.ensure_tool(cid, "社内システム")         # 既定は「不明」
    assert next(t for t in reg.list_tools(cid)).has_api == 2
    reg.ensure_tool(cid, "社内システム", has_api=1)
    assert next(t for t in reg.list_tools(cid)).has_api == 1


def test_metric_must_have_a_source(reg):
    cid = reg.upsert_company("A社")
    pid = reg.upsert_process(cid, "業務X")
    with pytest.raises(ValueError, match="出所が不正"):
        reg.set_metric(pid, "minutes_per_run", 10, "適当")


def test_metric_is_upserted_not_appended(reg):
    cid = reg.upsert_company("A社")
    pid = reg.upsert_process(cid, "業務X")
    reg.set_metric(pid, "minutes_per_run", 10, "declared")
    reg.set_metric(pid, "minutes_per_run", 12, "measured", evidence="実測しました")
    process = reg.get_process(pid)
    assert len(process.metrics) == 1
    assert process.metrics["minutes_per_run"].value == 12
    assert process.metrics["minutes_per_run"].source == "measured"
    assert process.metrics["minutes_per_run"].evidence == "実測しました"


def test_process_details_round_trip(reg):
    cid = reg.upsert_company("A社")
    tool = reg.ensure_tool(cid, "Excel", kind="software")
    pid = reg.upsert_process(
        cid, "台帳入力", department="業務課", owner="山田",
        has_data_entry=True, has_judgment=True, judgment_note="種別の判断",
        security_level="high",
    )
    reg.set_metric(pid, "minutes_per_run", 5, "measured")
    reg.set_metric(pid, "runs_per_month", 100, "measured")
    reg.set_process_tools(pid, [{"tool_id": tool, "role": "入力先"}])
    reg.set_process_steps(pid, [{"action": "入力する", "tool_id": tool, "input_info": "メール"}])
    reg.set_process_issues(pid, [{"kind": "error", "description": "打ち間違い"}])

    p = reg.get_process(pid)
    assert p.department_name == "業務課" and p.owner_name == "山田"
    assert p.has_judgment and p.judgment_note == "種別の判断"
    assert p.security_level == "high"
    assert [t.name for t in p.tools] == ["Excel"]
    assert p.steps[0].tool_name == "Excel"
    assert p.issues[0].kind == "error"
    assert p.monthly_minutes == 500


def test_deleting_a_process_removes_its_details(reg):
    cid = reg.upsert_company("A社")
    pid = reg.upsert_process(cid, "業務X")
    reg.set_metric(pid, "minutes_per_run", 5, "declared")
    reg.delete_process(pid)
    assert reg.get_process(pid) is None
    left = reg.conn.execute(
        "SELECT COUNT(*) c FROM process_metrics WHERE process_id=?", (pid,)
    ).fetchone()["c"]
    assert left == 0


def test_empty_names_are_rejected(reg):
    cid = reg.upsert_company("A社")
    for call in (
        lambda: reg.upsert_company(""),
        lambda: reg.ensure_department(cid, "  "),
        lambda: reg.ensure_tool(cid, ""),
        lambda: reg.upsert_process(cid, ""),
    ):
        with pytest.raises(ValueError):
            call()
