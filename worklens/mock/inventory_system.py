"""モック「社内管理システム」（転記元）。

実務の再現ポイント: **読み取り用のAPIがある**。
STEP2 の open_question「APIまたはCSV入出力が用意されているか」に対して
"YES" のケース。転記元は API で取れるため、ここは自動化が容易になる。
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .data import CARS

app = FastAPI(title="在庫管理システム（モック）")

STYLE = """
<style>
 body{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;
      background:#f4f6f9;color:#1b1f26;margin:0;font-size:14px}
 header{background:#1f3a5f;color:#fff;padding:14px 24px;font-weight:700}
 header small{display:block;font-weight:400;font-size:11px;opacity:.75}
 main{max-width:1000px;margin:0 auto;padding:24px}
 table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;
       box-shadow:0 1px 3px rgba(0,0,0,.08)}
 th,td{padding:10px 12px;border-bottom:1px solid #e6e9ee;text-align:left}
 th{background:#eef1f6;font-size:11px;color:#5a6472}
 .tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600}
 .yes{background:#e6f4ef;color:#17805a}.no{background:#fdf3e0;color:#b06f00}
 dl{display:grid;grid-template-columns:120px 1fr;gap:8px 16px;background:#fff;padding:20px;
    border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 dt{color:#6b7280;font-size:12px} dd{margin:0;font-weight:600}
 a{color:#1f5fd4}
</style>
"""


def _find(car_id: int) -> dict:
    for car in CARS:
        if car["id"] == car_id:
            return car
    raise HTTPException(status_code=404, detail="車両が見つかりません")


def _mileage(car: dict) -> str:
    km = car.get("mileage_km")
    return f"{km:,} km" if km else "未入力"


def _price(car: dict) -> str:
    if car.get("price_yen") is None:
        return car.get("price_text") or "—"
    return f"{car['price_yen']:,}円"


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/cars")


@app.get("/cars", response_class=HTMLResponse)
def car_list() -> str:
    rows = "".join(
        f"<tr><td><a href='/cars/{c['id']}'>{c['id']}</a></td><td>{c['maker']}</td>"
        f"<td>{c['model']}</td><td>{c['year']}年</td>"
        f"<td>{_mileage(c)}</td>"
        f"<td>{_price(c)}</td>"
        f"<td><span class='tag {'yes' if c['listed'] else 'no'}'>"
        f"{'掲載済' if c['listed'] else '未掲載'}</span></td></tr>"
        for c in CARS
    )
    return f"""{STYLE}<header>在庫管理システム<small>MOCK — STEP2練習用</small></header>
<main><h2>在庫一覧（{len(CARS)}台 / 未掲載 {sum(1 for c in CARS if not c['listed'])}台）</h2>
<table><tr><th>車両ID</th><th>メーカー</th><th>車種</th><th>年式</th><th>走行距離</th>
<th>価格</th><th>掲載状況</th></tr>{rows}</table></main>"""


@app.get("/cars/{car_id}", response_class=HTMLResponse)
def car_detail(car_id: int) -> str:
    c = _find(car_id)
    return f"""{STYLE}<header>在庫管理システム<small>MOCK — STEP2練習用</small></header>
<main><p><a href="/cars">← 在庫一覧</a></p><h2>車両詳細 {c['id']}</h2>
<dl><dt>メーカー</dt><dd>{c['maker']}</dd><dt>車種</dt><dd>{c['model']}</dd>
<dt>年式</dt><dd>{c['year']}年</dd>
<dt>走行距離</dt><dd>{_mileage(c)}</dd>
<dt>価格</dt><dd>{_price(c)}</dd><dt>色</dt><dd>{c['color']}</dd>
<dt>車検</dt><dd>{c['inspection']}</dd><dt>装備</dt><dd>{c['equipment']}</dd>
<dt>備考</dt><dd>{c['note'] or '—'}</dd>
<dt>掲載状況</dt><dd>{'掲載済' if c['listed'] else '未掲載'}</dd></dl></main>"""


# ------------------------------------------------------------------ API
@app.get("/api/cars")
def api_cars(listed: bool | None = None) -> JSONResponse:
    """転記元の読み取りAPI。listed=false で未掲載車両だけ取れる。"""
    cars = CARS if listed is None else [c for c in CARS if c["listed"] is listed]
    return JSONResponse(cars)


@app.get("/api/cars/{car_id}")
def api_car(car_id: int) -> JSONResponse:
    return JSONResponse(_find(car_id))


@app.post("/api/cars/{car_id}/listed")
def api_mark_listed(car_id: int) -> JSONResponse:
    car = _find(car_id)
    car["listed"] = True
    return JSONResponse({"ok": True, "id": car_id, "listed": True})
