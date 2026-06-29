# n8n ワークフロー

このフォルダはn8nのワークフローを **JSONでGit管理** する場所です。

## 運用ルール

- n8n上で編集したら **Export → ここへ上書き保存 → commit** する。
- 認証情報はワークフローJSONに埋め込まず、n8nの **Credentials** または `.env` を参照する。
- 1ワークフロー=1つの目的。

## ワークフロー一覧

| ファイル | 起点(トリガー) | 目的 |
|---|---|---|
| `ceo-orchestrator.json` | 手動 / Webhook | オーナーのゴールをCEOに渡し全体を起動 |
| `content-pipeline.json` | CEOから呼出 | 企画→本文→デザイン→予約→（承認）→投稿 |

## インポート方法

1. n8n を開く → 右上 **⋮ → Import from File**
2. このフォルダのJSONを選択
3. Credentials を各自の環境に紐付け
4. 環境変数（`N8N_*`, SNS系）を設定
