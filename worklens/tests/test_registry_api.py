"""登録系の画面とAPIの検証。"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from worklens.storage.registry_repo import RegistryRepo


@pytest.fixture()
def reg(repos):
    return RegistryRepo(repos.conn)


@pytest.fixture()
def client(org, reg):
    from worklens.api.app import app

    return TestClient(app)


def test_registration_screens_render(client):
    for path in ("/registry", "/processes"):
        res = client.get(path)
        assert res.status_code == 200, path


def test_registry_screen_shows_the_generic_master_fields(client):
    body = client.get("/registry").text
    for label in ("業種", "従業員数", "部署", "担当者", "使用PC", "使用ソフト"):
        assert label in body


def test_company_can_be_registered_from_the_screen(client, reg):
    res = client.post("/api/registry/companies", data={
        "name": "新規商事", "industry": "不動産", "employee_count": "42",
        "hourly_cost_jpy": "4000",
    })
    assert res.status_code == 200
    company = reg.get_company(res.json()["company_id"])
    assert company.name == "新規商事"
    assert company.industry_name == "不動産"
    assert company.employee_count == 42


def test_industry_is_registered_not_hardcoded(client, reg):
    """業種は登録制。コードに選択肢を埋め込まない。"""
    for industry in ("士業", "介護", "製造"):
        client.post("/api/registry/companies",
                    data={"name": f"{industry}の会社", "industry": industry})
    names = {i["name"] for i in reg.list_industries()}
    assert {"士業", "介護", "製造"} <= names


def test_masters_can_be_added(client, reg, org):
    cid = org["company_id"]
    client.post(f"/api/registry/companies/{cid}/master",
                data={"kind": "department", "name": "総務課"})
    client.post(f"/api/registry/companies/{cid}/master",
                data={"kind": "staff", "name": "田中", "extra1": "総務課", "extra2": "課長"})
    client.post(f"/api/registry/companies/{cid}/master",
                data={"kind": "workplace", "name": "PC-99", "extra1": "Windows 11"})
    client.post(f"/api/registry/companies/{cid}/master",
                data={"kind": "tool", "name": "kintone", "extra1": "web_service",
                      "extra2": "あり"})
    assert [d["name"] for d in reg.list_departments(cid)] == ["総務課"]
    assert reg.list_staff(cid)[0]["job_title"] == "課長"
    assert reg.list_workplaces(cid)[0]["os"] == "Windows 11"
    tool = reg.list_tools(cid)[0]
    assert tool.kind == "web_service" and tool.has_api == 1


def test_unknown_master_kind_is_refused(client, org):
    res = client.post(f"/api/registry/companies/{org['company_id']}/master",
                      data={"kind": "宇宙船", "name": "x"})
    assert res.status_code == 400


def test_process_can_be_registered_manually(client, reg, org):
    res = client.post("/api/processes", data={
        "company_id": org["company_id"], "name": "請求書の作成",
        "department": "経理課", "owner": "高橋",
        "minutes_per_run": "15", "runs_per_month": "60",
        "metric_source": "申告", "evidence": "担当者ヒアリング",
        "has_data_entry": "1", "has_transcription": "1",
        "tools": "Excel / 会計システム", "steps": "集計 → 作成 → 送付",
        "issues": "月末に集中する",
    })
    assert res.status_code == 200
    process = reg.get_process(res.json()["process_id"])
    assert process.name == "請求書の作成"
    assert process.department_name == "経理課"
    assert process.monthly_minutes == 900
    assert process.metrics["minutes_per_run"].source == "declared"
    assert process.metrics["minutes_per_run"].evidence == "担当者ヒアリング"
    assert [t.name for t in process.tools] == ["Excel", "会計システム"]
    assert len(process.steps) == 3
    assert process.origin == "manual"


def test_manual_registration_requires_the_core_numbers(client, org):
    """足りない項目を日本語で返す（汎用の422ではなく、何が足りないかを言う）。"""
    res = client.post("/api/processes", data={
        "company_id": org["company_id"], "name": "数値なしの業務",
    })
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "1回あたり作業時間" in detail and "月間実行回数" in detail


def test_manual_registration_refuses_an_unreadable_source(client, org):
    res = client.post("/api/processes", data={
        "company_id": org["company_id"], "name": "業務",
        "minutes_per_run": "5", "runs_per_month": "10",
        "metric_source": "なんとなく",
    })
    assert res.status_code == 400
    assert "出所" in res.json()["detail"]


def test_csv_upload_imports_and_reports_warnings(client, reg, org):
    csv_text = (
        "業務名,作業時間,月間回数,出所,謎の列\n"
        "業務A,5,10,実測,x\n"
        ",8,20,申告,y\n"
    )
    res = client.post(
        "/api/registry/import/csv",
        data={"company_id": org["company_id"]},
        files={"file": ("work.csv", csv_text.encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 1 and body["skipped"] == 1
    assert any("謎の列" in w for w in body["warnings"])
    assert any("業務名が空です" in e for e in body["errors"])


def test_csv_upload_accepts_cp932(client, reg, org):
    """Excelから出したCSVはShift_JISのことが多い。"""
    csv_text = "業務名,作業時間,月間回数\n見積作成,7,30\n"
    res = client.post(
        "/api/registry/import/csv",
        data={"company_id": org["company_id"]},
        files={"file": ("sjis.csv", csv_text.encode("cp932"), "text/csv")},
    )
    assert res.status_code == 200 and res.json()["created"] == 1
    assert reg.list_processes(org["company_id"])[0].name == "見積作成"


def test_json_upload_can_create_the_company_itself(client, reg):
    payload = {
        "company": {"name": "JSON商事", "industry": "物流"},
        "processes": [{"name": "配車入力",
                       "metrics": {"minutes_per_run": {"value": 6, "source": "実測"},
                                   "runs_per_month": {"value": 200, "source": "実測"}}}],
    }
    res = client.post(
        "/api/registry/import/json", data={},
        files={"file": ("w.json", json.dumps(payload, ensure_ascii=False).encode(), "application/json")},
    )
    assert res.status_code == 200
    company = reg.get_company(res.json()["company_id"])
    assert company.name == "JSON商事" and company.industry_name == "物流"


def test_sample_can_be_loaded_from_the_screen(client, reg):
    res = client.post("/api/registry/import/sample", data={"key": "used_car_dealer"})
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 8
    assert len(reg.list_processes(body["company_id"])) == 8


def test_unknown_sample_is_reported(client):
    res = client.post("/api/registry/import/sample", data={"key": "ない業種"})
    assert res.status_code == 404


def test_process_detail_shows_every_number_with_its_source(client, reg):
    body = client.post("/api/registry/import/sample", data={"key": "used_car_dealer"}).json()
    process = max(reg.list_processes(body["company_id"]), key=lambda p: p.monthly_minutes)
    page = client.get(f"/processes/{process.id}").text
    assert process.name in page
    assert "計測値と出所" in page
    assert "実測" in page and "申告" in page
    assert "出所の無い数値は登録できません" in page


def test_process_list_shows_data_confidence(client, reg):
    client.post("/api/registry/import/sample", data={"key": "used_car_dealer"})
    page = client.get("/processes").text
    assert "データの確度" in page
    assert "月間作業時間" in page


def test_process_can_be_deleted(client, reg, org):
    res = client.post("/api/processes", data={
        "company_id": org["company_id"], "name": "消す業務",
        "minutes_per_run": "5", "runs_per_month": "10",
    })
    pid = res.json()["process_id"]
    assert client.post(f"/api/processes/{pid}/delete").status_code == 200
    assert reg.get_process(pid) is None


def test_missing_process_returns_404(client):
    assert client.get("/processes/deadbeef").status_code == 404
