"""モック「問い合わせメール受信箱」（転記元）。

候補2位「メールで受けた内容をExcelへ入力」の練習用。
実務の再現ポイント: **本文が自由文**で、書式が揃っていない。
だから正規表現だけでは限界があり、AI（LLM）による抽出が効いてくる。

そのままでは自動化できないメールも混ぜてある
（予算が書かれていない / クレーム / 複数台の問い合わせ）。
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="問い合わせ受信箱（モック）")

MESSAGES: list[dict] = [
    {
        "id": "m001", "received_at": "2025-06-02T09:14:00+09:00",
        "from": "tanaka.k@example.com", "subject": "アクアの在庫について",
        "body": (
            "はじめまして。田中健一と申します。\n"
            "御社サイトで拝見したトヨタ アクア（2019年式）の購入を検討しています。\n"
            "予算は130万円程度、できれば今月中に現車を確認したいです。\n"
            "連絡先は 090-1111-2222 です。よろしくお願いいたします。"
        ),
        "processed": False, "replied": False,
    },
    {
        "id": "m002", "received_at": "2025-06-02T10:02:00+09:00",
        "from": "sato_m@example.net", "subject": "見積もり希望",
        "body": (
            "佐藤美咲です。\n"
            "ホンダのフィットで、予算は150万円まで見ています。\n"
            "電話は 080-3333-4444、平日夕方以降だと助かります。"
        ),
        "processed": False,
    },
    {
        "id": "m003", "received_at": "2025-06-02T11:30:00+09:00",
        "from": "y.suzuki@example.org", "subject": "N-BOXの件",
        "body": (
            "鈴木裕子と申します。N-BOXを探しています。\n"
            "予算は120万円ほどです。070-5555-6666 までご連絡ください。"
        ),
        "processed": False,
    },
    # --- 例外1: 予算が書かれていない ---
    {
        "id": "m004", "received_at": "2025-06-02T13:05:00+09:00",
        "from": "takahashi@example.com", "subject": "SUVを探しています",
        "body": (
            "高橋と申します。SUVで良いものがあれば教えてください。\n"
            "予算はまだ決めていません。まずは選択肢を知りたいです。\n"
            "090-7777-8888"
        ),
        "processed": False,
    },
    # --- 例外2: クレーム（営業台帳に入れる話ではない） ---
    {
        "id": "m005", "received_at": "2025-06-02T14:20:00+09:00",
        "from": "ito.claim@example.com", "subject": "先日の対応について",
        "body": (
            "先週伺った際の担当者の対応について申し上げたいことがあります。\n"
            "至急、責任者の方からご連絡をいただけますでしょうか。\n"
            "伊藤 090-9999-0000"
        ),
        "processed": False,
    },
    # --- 例外3: 複数台の問い合わせ（1行に落とせない） ---
    {
        "id": "m006", "received_at": "2025-06-02T15:45:00+09:00",
        "from": "watanabe@example.jp", "subject": "社用車の入れ替え",
        "body": (
            "渡辺です。社用車の入れ替えで、ハイエースを2台とセレナを1台、\n"
            "合計3台の見積をお願いしたいです。予算は全体で600万円です。\n"
            "03-1234-5678（会社代表）"
        ),
        "processed": False,
    },
    {
        "id": "m007", "received_at": "2025-06-02T16:10:00+09:00",
        "from": "nakamura.t@example.com", "subject": "ハスラーの試乗",
        "body": (
            "中村と申します。ハスラーの試乗を希望します。\n"
            "予算は145万円くらいで考えています。連絡先 090-2222-3333。"
        ),
        "processed": False,
    },
]

STYLE = """
<style>
 body{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;
      background:#f2f4f8;color:#1b1f26;margin:0;font-size:14px}
 header{background:#0f5132;color:#fff;padding:14px 24px;font-weight:700}
 header small{display:block;font-weight:400;font-size:11px;opacity:.8}
 main{max-width:900px;margin:0 auto;padding:24px}
 .msg{background:#fff;border-radius:8px;padding:16px 18px;margin-bottom:12px;
      box-shadow:0 1px 3px rgba(0,0,0,.08)}
 .msg h3{margin:0 0 4px;font-size:14px}
 .meta{color:#6b7280;font-size:12px;margin-bottom:8px}
 pre{white-space:pre-wrap;margin:0;font-family:inherit;font-size:13px;line-height:1.7}
 .tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600}
 .done{background:#e6f4ef;color:#17805a}.todo{background:#fdf3e0;color:#b06f00}
</style>
"""


def _find(message_id: str) -> dict:
    for m in MESSAGES:
        if m["id"] == message_id:
            return m
    raise HTTPException(status_code=404, detail="メールが見つかりません")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/inbox")


@app.get("/inbox", response_class=HTMLResponse)
def inbox() -> str:
    items = "".join(
        f"<div class='msg'><h3>{m['subject']} "
        f"<span class='tag {'done' if m['processed'] else 'todo'}'>"
        f"{'台帳登録済' if m['processed'] else '未処理'}</span></h3>"
        f"<div class='meta'>{m['from']} ／ {m['received_at'][:16].replace('T', ' ')}</div>"
        f"<pre>{m['body']}</pre></div>"
        for m in MESSAGES
    )
    todo = sum(1 for m in MESSAGES if not m["processed"])
    return f"""{STYLE}<header>問い合わせ受信箱<small>MOCK — STEP2練習用</small></header>
<main><h2>受信メール（{len(MESSAGES)}件 / 未処理 {todo}件）</h2>{items}</main>"""


@app.get("/api/messages")
def api_messages(processed: bool | None = None) -> JSONResponse:
    items = MESSAGES if processed is None else [
        m for m in MESSAGES if m["processed"] is processed
    ]
    return JSONResponse([{**m, "replied": bool(m.get("replied"))} for m in items])


@app.post("/api/messages/{message_id}/replied")
def api_mark_replied(message_id: str) -> JSONResponse:
    message = _find(message_id)
    message["replied"] = True
    return JSONResponse({"ok": True, "id": message_id, "replied": True})


@app.post("/api/messages/{message_id}/processed")
def api_mark_processed(message_id: str) -> JSONResponse:
    message = _find(message_id)
    message["processed"] = True
    return JSONResponse({"ok": True, "id": message_id})


# ------------------------------------------------------------ 下書き
# 自動化は「下書きを作る」ところまで。送信ボタンは人が押す。
# 誤送信は取り返しがつかないので、構造として送信させない。
DRAFTS: list[dict] = []


@app.get("/drafts", response_class=HTMLResponse)
def draft_list() -> str:
    items = "".join(
        f"<div class='msg'><h3>{d['subject']} "
        f"<span class='tag {'done' if d.get('sent') else 'todo'}'>"
        f"{'送信済' if d.get('sent') else '送信待ち（人が確認）'}</span></h3>"
        f"<div class='meta'>宛先 {d['to']}"
        f"{' ／ 添付 ' + d['attachment'] if d.get('attachment') else ''}</div>"
        f"<pre>{d['body']}</pre></div>"
        for d in DRAFTS
    )
    waiting = sum(1 for d in DRAFTS if not d.get("sent"))
    return f"""{STYLE}<header>問い合わせ受信箱<small>MOCK — STEP2練習用</small></header>
<main><h2>下書き（{len(DRAFTS)}件 / 送信待ち {waiting}件）</h2>
<p>自動化が作成した下書きです。<b>送信は人が確認してから行います。</b></p>
{items or '<p>下書きはありません。</p>'}</main>"""


@app.get("/api/drafts")
def api_drafts() -> JSONResponse:
    return JSONResponse(DRAFTS)


@app.post("/api/drafts")
def api_create_draft(draft: dict) -> JSONResponse:
    for required in ("to", "subject", "body"):
        if not str(draft.get(required) or "").strip():
            raise HTTPException(status_code=400, detail=f"{required} が空です")
    key = draft.get("ref")
    if key and any(d.get("ref") == key for d in DRAFTS):
        raise HTTPException(status_code=409, detail=f"{key} の下書きは既にあります")
    record = {
        "id": f"d{len(DRAFTS) + 1:03d}",
        "to": draft["to"], "subject": draft["subject"], "body": draft["body"],
        "attachment": draft.get("attachment"), "ref": key, "sent": False,
    }
    DRAFTS.append(record)
    return JSONResponse(record)


@app.post("/_reset", include_in_schema=False)
def reset() -> dict:
    for m in MESSAGES:
        m["processed"] = False
        m.pop("replied", None)
    DRAFTS.clear()
    return {"ok": True}
