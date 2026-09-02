"""モック「通知先」（Slack / Teams の代わり）。

確認・モニタリング業務の練習用。
毎回目視で確認していたものを、**条件に合致したときだけ通知**へ置き換える。
通知が来ないこと自体が「異常なし」の情報になる。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="通知先（モック）")

NOTIFICATIONS: list[dict] = []

STYLE = """
<style>
 body{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;
      background:#f6f4fb;color:#1b1f26;margin:0;font-size:14px}
 header{background:#4a2f8f;color:#fff;padding:14px 24px;font-weight:700}
 header small{display:block;font-weight:400;font-size:11px;opacity:.8}
 main{max-width:820px;margin:0 auto;padding:24px}
 .n{background:#fff;border-radius:8px;padding:14px 16px;margin-bottom:10px;
    box-shadow:0 1px 3px rgba(0,0,0,.08);border-left:4px solid #4a2f8f}
 .n.warn{border-left-color:#b06f00}.n.info{border-left-color:#1f5fd4}
 .n h3{margin:0 0 4px;font-size:14px}
 .meta{color:#6b7280;font-size:12px}
 pre{white-space:pre-wrap;margin:6px 0 0;font-family:inherit;font-size:13px}
</style>
"""


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/notifications")


@app.get("/notifications", response_class=HTMLResponse)
def view() -> str:
    items = "".join(
        f"<div class='n {n.get('level', 'info')}'><h3>{n['title']}</h3>"
        f"<div class='meta'>{n['created_at'][:19].replace('T', ' ')}</div>"
        f"<pre>{n['body']}</pre></div>"
        for n in reversed(NOTIFICATIONS)
    )
    return f"""{STYLE}<header>通知先<small>MOCK — STEP2練習用</small></header>
<main><h2>通知（{len(NOTIFICATIONS)}件）</h2>
<p>条件に合致したときだけ届きます。届かないこと自体が「異常なし」の情報です。</p>
{items or '<p>通知はありません。</p>'}</main>"""


@app.get("/api/notifications")
def api_list() -> JSONResponse:
    return JSONResponse(NOTIFICATIONS)


@app.post("/api/notifications")
def api_notify(payload: dict) -> JSONResponse:
    record = {
        "id": f"n{len(NOTIFICATIONS) + 1:03d}",
        "title": payload.get("title") or "(件名なし)",
        "body": payload.get("body") or "",
        "level": payload.get("level") or "info",
        "ref": payload.get("ref"),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    NOTIFICATIONS.append(record)
    return JSONResponse(record)


@app.post("/_reset", include_in_schema=False)
def reset() -> dict:
    NOTIFICATIONS.clear()
    return {"ok": True}
