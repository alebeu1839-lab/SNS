"""モック「掲載サイト」（転記先）。

実務の再現ポイント: **APIが無い**。登録はブラウザのフォーム経由のみ。
STEP2 の open_question に対して "NO" のケースで、RPA（ブラウザ自動操作）が
必要になる。転記元はAPI・転記先はフォーム、という実務で最も多い組み合わせ。

バリデーションもある（必須項目・価格は数値のみ）。自動化はこれを
踏み越えず、通らないものは人へ引き継がなければならない。
"""
from __future__ import annotations

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(title="掲載管理コンソール（モック）")

LISTINGS: list[dict] = []

STYLE = """
<style>
 body{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;
      background:#fbf7f2;color:#22201c;margin:0;font-size:14px}
 header{background:#8a4b12;color:#fff;padding:14px 24px;font-weight:700}
 header small{display:block;font-weight:400;font-size:11px;opacity:.8}
 main{max-width:920px;margin:0 auto;padding:24px}
 form{background:#fff;padding:22px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08);
      display:grid;grid-template-columns:150px 1fr;gap:12px 16px;align-items:center}
 label{color:#6b6257;font-size:12px}
 input,textarea{padding:8px 10px;border:1px solid #ddd5c9;border-radius:6px;font-size:14px;
                font-family:inherit;width:100%;box-sizing:border-box}
 .actions{grid-column:1/-1;text-align:right;margin-top:8px}
 button{background:#8a4b12;color:#fff;border:0;padding:9px 22px;border-radius:6px;
        font-size:14px;cursor:pointer;font-family:inherit}
 table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;
       box-shadow:0 1px 3px rgba(0,0,0,.08)}
 th,td{padding:10px 12px;border-bottom:1px solid #eee6db;text-align:left}
 th{background:#f3ece2;font-size:11px;color:#6b6257}
 .err{grid-column:1/-1;background:#fdeceb;color:#b3261e;padding:10px 14px;border-radius:6px;
      font-size:13px}
 .ok{background:#e6f4ef;color:#0d5b40;padding:10px 14px;border-radius:6px;margin-bottom:16px}
 a{color:#8a4b12}
</style>
"""

REQUIRED = ("vehicle_id", "maker", "model", "year", "mileage", "price", "color")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/vehicles")


@app.get("/vehicles", response_class=HTMLResponse)
def listing_index(registered: str | None = None) -> str:
    rows = "".join(
        f"<tr><td>{v['vehicle_id']}</td><td>{v['maker']}</td><td>{v['model']}</td>"
        f"<td>{v['year']}</td><td>{v['mileage']}</td><td>{v['price']}</td>"
        f"<td>{v['color']}</td></tr>"
        for v in LISTINGS
    )
    banner = (
        f"<div class='ok'>車両ID {registered} を掲載しました。</div>" if registered else ""
    )
    body = (
        f"<table><tr><th>車両ID</th><th>メーカー</th><th>車種</th><th>年式</th>"
        f"<th>走行距離</th><th>価格</th><th>色</th></tr>{rows}</table>"
        if LISTINGS
        else "<p>まだ掲載されている車両はありません。</p>"
    )
    return f"""{STYLE}<header>掲載管理コンソール<small>MOCK — STEP2練習用</small></header>
<main>{banner}<h2>掲載中の車両（{len(LISTINGS)}台）</h2>
<p><a href="/vehicles/new">＋ 車両情報 新規登録</a></p>{body}</main>"""


@app.get("/vehicles/new", response_class=HTMLResponse)
def new_form(error: str | None = None) -> str:
    err = f"<div class='err'>{error}</div>" if error else ""
    return f"""{STYLE}<header>掲載管理コンソール<small>MOCK — STEP2練習用</small></header>
<main><h2>車両情報 新規登録</h2>
<form method="post" action="/vehicles">{err}
 <label for="vehicle_id">車両ID *</label><input id="vehicle_id" name="vehicle_id">
 <label for="maker">メーカー *</label><input id="maker" name="maker">
 <label for="model">車種 *</label><input id="model" name="model">
 <label for="year">年式 *</label><input id="year" name="year">
 <label for="mileage">走行距離(km) *</label><input id="mileage" name="mileage">
 <label for="price">価格(円) *</label><input id="price" name="price">
 <label for="color">色 *</label><input id="color" name="color">
 <label for="inspection">車検</label><input id="inspection" name="inspection">
 <label for="equipment">装備</label><input id="equipment" name="equipment">
 <label for="comment">コメント</label><textarea id="comment" name="comment" rows="3"></textarea>
 <div class="actions"><button type="submit" id="submit">この内容で掲載する</button></div>
</form></main>"""


@app.post("/vehicles")
def register(
    vehicle_id: str = Form(default=""),
    maker: str = Form(default=""),
    model: str = Form(default=""),
    year: str = Form(default=""),
    mileage: str = Form(default=""),
    price: str = Form(default=""),
    color: str = Form(default=""),
    inspection: str = Form(default=""),
    equipment: str = Form(default=""),
    comment: str = Form(default=""),
) -> RedirectResponse:
    values = {
        "vehicle_id": vehicle_id.strip(), "maker": maker.strip(), "model": model.strip(),
        "year": year.strip(), "mileage": mileage.strip(), "price": price.strip(),
        "color": color.strip(), "inspection": inspection.strip(),
        "equipment": equipment.strip(), "comment": comment.strip(),
    }
    missing = [k for k in REQUIRED if not values[k]]
    if missing:
        return RedirectResponse(
            f"/vehicles/new?error=必須項目が未入力です: {','.join(missing)}", status_code=303
        )
    for numeric in ("year", "mileage", "price"):
        if not values[numeric].replace(",", "").isdigit():
            return RedirectResponse(
                f"/vehicles/new?error={numeric} は数値で入力してください（受信値: "
                f"{values[numeric]}）",
                status_code=303,
            )
    if any(v["vehicle_id"] == values["vehicle_id"] for v in LISTINGS):
        return RedirectResponse(
            f"/vehicles/new?error=車両ID {values['vehicle_id']} は既に掲載済みです",
            status_code=303,
        )
    LISTINGS.append(values)
    return RedirectResponse(f"/vehicles?registered={values['vehicle_id']}", status_code=303)


@app.post("/_reset", include_in_schema=False)
def reset() -> dict:
    """テスト用。掲載済みデータを消す。"""
    LISTINGS.clear()
    return {"ok": True}
