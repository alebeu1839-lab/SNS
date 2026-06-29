# 組織図

```
                  人間オーナー（最終意思決定・承認）
                          │
                    ┌─────▼─────┐
                    │    CEO     │  orchestrator
                    └─────┬─────┘
      ┌──────────┬────────┼────────┬──────────┬───────────────┐
      ▼          ▼        ▼        ▼          ▼               ▼
 content-     copywriter designer scheduler publisher  analyst   community-
 planner      (writing)  (visual)(scheduling)(publishing)(analytics) manager
 (planning)                                                      (engagement)
```

## 指揮系統のルール

- すべての社員は **CEOにのみ** レポートする（`reportsTo: ceo`）。
- CEOだけが人間オーナー直下（`reportsTo: null`）。
- 社員同士は命令しない。連携は「成果物の受け渡し（handoff）」のみ。

## 役割一覧

| id | role | 一言で |
|---|---|---|
| ceo | orchestrator | 分解して振る |
| content-planner | planning | 企画を出す |
| copywriter | writing | 本文を書く |
| designer | visual | 画像を作る |
| scheduler | scheduling | 日時を決める |
| publisher | publishing | 投稿する（唯一の公開権限） |
| analyst | analytics | 数値を分析する |
| community-manager | engagement | コメント/DMに返す |

> 役割を増やすときは [onboarding.md](onboarding.md) を参照。
