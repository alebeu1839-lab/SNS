# n8n ワークフロー

このフォルダはn8nのワークフローを **JSONでGit管理** する場所です。
スケルトンではなく、Anthropic APIを実際に呼び出して各AIエージェントを動かす実装です。

## ワークフロー一覧

| ファイル | 種別 | 役割 |
|---|---|---|
| `agent-runner.json` | **エンジン（再利用）** | `{agentId, task, context}` を受け取り、`agents/<id>/prompt.md` と `agent.config.json` を読み込み、Anthropic Messages API を呼び、出力JSON `{result, handoffTo}` を返す |
| `ceo-orchestrator.json` | 起点 | Webhookでゴール受信 → CEOが `assignments` 生成 → 各担当へ振り分け実行 → 結果返却 |
| `content-pipeline.json` | パイプライン | 企画→本文→デザイン→予約→(承認)→投稿→分析。各ステップが `agent-runner` を呼ぶ |
| `publish-dispatch.json` | **投稿ルーター** | `{scheduled, approved}` を受け、`channels.yaml` で platform を解決し有効チャンネルを各 `publish-*` へ振り分け |
| `publish-x.json` | 実投稿（X） | `approved=true` のときだけ x-main 宛をX API v2へ送信。未承認は中止 |
| `publish-meta.json` | 実投稿（Instagram） | Graph API の2段階（media作成→publish）。画像URLとキャプションが必要 |
| `publish-tiktok.json` | 実投稿（TikTok） | Content Posting API。動画URLが必要。既定公開範囲は SELF_ONLY |

すべて `agent-runner.json` を呼ぶ構造なので、**AI社員を増やしてもワークフロー本体の改修は不要**です。

## アーキテクチャ（実行時）

```
Webhook(goal) ─▶ CEO Orchestrator ─▶ agent-runner(ceo) ─▶ assignments
                                          │
                  ┌───────────────────────┴─ split ──────────────┐
                  ▼                                               ▼
            agent-runner(content-planner) ... または Content Pipeline 全体
                  │
   agent-runner が各 prompt.md を読み Anthropic API を呼ぶ
```

## セットアップ（重要）

これらのワークフローは **リポジトリのファイルをn8nが読む** 前提です。次を満たしてください。

### 1. リポジトリをn8nコンテナにマウント
```yaml
# docker-compose.yml（例）
services:
  n8n:
    image: docker.n8n.io/n8nio/n8n
    volumes:
      - ./:/data/repo:ro          # このリポジトリを /data/repo にマウント
    environment:
      - AGENT_REPO_PATH=/data/repo
      - NODE_FUNCTION_ALLOW_BUILTIN=fs        # Codeノードで fs を使うため
      - N8N_BLOCK_ENV_ACCESS_IN_NODE=false    # $env を使うため
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - DEFAULT_MODEL=${DEFAULT_MODEL}
      - HUMAN_APPROVAL_REQUIRED=${HUMAN_APPROVAL_REQUIRED}
      - NOTIFY_WEBHOOK_URL=${NOTIFY_WEBHOOK_URL}
```

### 2. 必要な環境変数（`.env.example` 参照）
- `ANTHROPIC_API_KEY` … Anthropic APIキー
- `DEFAULT_MODEL` … 既定モデル（各 `agent.config.json` で上書き可）
- `AGENT_REPO_PATH` … マウント先（既定 `/data/repo`）
- `HUMAN_APPROVAL_REQUIRED` … `true` なら投稿前に承認待ち
- `NOTIFY_WEBHOOK_URL` … 承認依頼の通知先（Slack等）

### 3. インポート
1. n8n → **⋮ → Import from File** で各JSONを取り込む
2. `agent-runner` 以外は `agent-runner.json` を **localFile** 参照で呼ぶ設定済み。
   n8nのバージョンによっては Execute Workflow ノードを開いて
   ソース（File / 指定パス）を再確認してください。
3. **X実投稿の認証**: n8nで `twitterOAuth2Api` クレデンシャル（tweet.write 権限）を作成し、
   `publish-x.json` の "Create Tweet (X)" ノードに紐付ける（インポート後に1度選択）。
   Meta / TikTok は同じ入出力 `{scheduled, approved}` で `publish-meta.json` 等を追加すれば拡張可能。
4. 画像添付は次の層: `assets` のURLをXのmedia uploadに通してから添付する（テキスト投稿は本実装で動作）。

## 動作確認

```bash
# CEOにゴールを投げる
curl -X POST "$N8N_WEBHOOK_BASE_URL/webhook/ceo-goal" \
  -H 'content-type: application/json' \
  -d '{"goal":"新商品の告知キャンペーンを今週中に回して","constraints":{"channels":["x-main"]}}'
```

## 運用ルール

- n8n上で編集したら **Export → ここへ上書き → commit**。
- 認証情報はJSONに埋め込まず、`$env` / n8n Credentials を参照する。
- 1ワークフロー=1つの目的。
