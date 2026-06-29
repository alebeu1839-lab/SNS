# 運用開始ガイド（Go Live）

「**SNSアカウントを繋げば運用が始まる**」状態にするための手順書です。
コードは完成しているので、あなたがやるのは **接続と有効化だけ** です。

```
[1] 環境を起動  →  [2] アカウントを繋ぐ  →  [3] 有効化  →  [4] 接続確認  →  [5] 稼働
```

---

## 1. 環境を起動する

```bash
cp .env.example .env      # 値はこのあと埋める
docker compose up -d      # http://localhost:5678 で n8n が起動
```

n8n を開いて `workflows/*.json` をすべて Import します（Import from File）。

---

## 2. SNSアカウントを繋ぐ（=認証情報を設定）

各プラットフォームの認証情報を `.env` に記入します。**繋ぎたいアカウントのものだけでOK。**

| プラットフォーム | 取得元 | `.env` のキー |
|---|---|---|
| **X (Twitter)** | [X Developer Portal](https://developer.x.com/) でアプリ作成 → Keys & Tokens | `X_API_KEY` `X_API_SECRET` `X_ACCESS_TOKEN` `X_ACCESS_TOKEN_SECRET` |
| **Instagram** | [Meta for Developers](https://developers.facebook.com/) → Graph API、IGビジネスアカウント連携 | `META_ACCESS_TOKEN` `IG_BUSINESS_ACCOUNT_ID` |
| **TikTok** | [TikTok for Developers](https://developers.tiktok.com/) → Content Posting API | `TIKTOK_ACCESS_TOKEN` |

さらに **AIモデル**（必須）:

| 用途 | キー |
|---|---|
| AIエージェント実行 | `ANTHROPIC_API_KEY` |

> X は OAuth 署名の都合で、n8n の **Credentials**（`twitterOAuth2Api`）を作成して
> `publish-x.json` の "Create Tweet (X)" ノードに紐付けるのが確実です。
> Instagram/TikTok は `.env` のトークンを HTTP ノードがそのまま使います。

---

## 3. 運用するチャンネルを有効化する

`config/channels.yaml` で、繋いだアカウントの `enabled` を `true` にします。

```yaml
  - id: instagram-main
    platform: instagram
    enabled: true        # ← false から true へ
```

> `handle` も自分のアカウント名に変更しておきましょう。投稿頻度は `postingRules` で調整。

---

## 4. 接続を確認する（preflight）

```bash
node --env-file=.env scripts/check-connections.mjs
```

各チャンネルが `● READY / ▲ NEEDS SETUP / ○ DISABLED` で表示されます。
**繋ぎたいチャンネルがすべて `● READY` になり、`ANTHROPIC_API_KEY` も ✓** なら準備完了です。

```
x-main (x) @your_brand  ● READY
   ✓ X_API_KEY
   ...
結果: 1/1 の有効チャンネルが運用可能
▶ 運用可能。content-pipeline を回せます。
```

---

## 5. 稼働させる

1. n8n で `content-pipeline` と各 `publish-*` ワークフローを **Active** にする。
2. `ceo-orchestrator` の Webhook にゴールを投げると全工程が走ります:

```bash
curl -X POST "$N8N_WEBHOOK_BASE_URL/webhook/ceo-goal" \
  -H 'content-type: application/json' \
  -d '{"goal":"新商品の告知を今週中に","constraints":{"channels":["x-main"]}}'
```

3. `HUMAN_APPROVAL_REQUIRED=true` の場合、投稿直前に承認待ちになります
   （`NOTIFY_WEBHOOK_URL` に通知 → n8n の Wait を再開で投稿実行）。

---

## 投稿の流れ（どのアカウントにも自動で振り分く）

```
content-pipeline
   └─ scheduler が channel付きで予約
   └─ publish-dispatch が channels.yaml を見て platform を解決
        ├─ x-main        → publish-x.json     （X API v2）
        ├─ instagram-main→ publish-meta.json  （Graph API）
        └─ tiktok-main   → publish-tiktok.json（Content Posting API）
```

**新しいSNSを足す場合**: `config/channels.yaml` にチャンネルを追加し、
`workflows/publish-<platform>.json`（入出力は `{scheduled, approved}`）を作るだけ。
ディスパッチャや他の部分の改修は不要です。

---

## 安全装置

- すべての `publish-*` は `approved=true` のときだけ投稿します（未承認は必ず中止）。
- `enabled: false` のチャンネルには投稿しません。
- TikTok の既定公開範囲は安全側の `SELF_ONLY`。本番公開時に変更してください。
