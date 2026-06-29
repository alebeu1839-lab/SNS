# アーキテクチャ

## 全体像

```
┌─────────────┐
│ 人間オーナー  │  ゴールを与える / 承認する
└──────┬──────┘
       │
┌──────▼───────────────────────────────────────────┐
│  n8n (オーケストレーション層)                       │
│  - ceo-orchestrator workflow が起点                 │
│  - 各エージェントを AI ノードとして呼ぶ              │
│  - エージェント間は handoff.schema.json で受け渡す  │
└──────┬───────────────────────────────────────────┘
       │ assignments
┌──────▼──────┐
│  CEO Agent   │  タスク分解・振り分け
└──────┬──────┘
   ┌───┴────┬────────┬────────┬─────────┬────────┐
   ▼        ▼        ▼        ▼         ▼        ▼
 planner  copy    designer scheduler publisher analyst / community-mgr
   │        │        │        │         │
   └────────┴────────┴────────┴─────────┘  標準パイプライン
```

## レイヤー構成

| レイヤー | 実体 | 役割 |
|---|---|---|
| 設定 | `config/*.yaml`, `.env` | 社員登録簿・チャンネル・秘密情報 |
| 知識 | `agents/*/manual.md`, `core/prompts/` | 各社員のSOPと共通ルール |
| 振る舞い | `agents/*/prompt.md`, `agent.config.json` | AIへのシステムプロンプトとモデル設定 |
| 実行 | `workflows/*.json` (n8n) | 実際の自動実行とAPI連携 |
| 統治 | `.github/workflows/ci.yml` | レジストリとマニュアルの整合性検査 |

## データフロー（標準パイプライン）

1. オーナー → CEO: `goal`
2. CEO → planner: 企画依頼 → `ideas`
3. planner → copywriter: `copies`
4. copywriter → designer: `assets`
5. designer → scheduler: `scheduled`
6.（人間承認）→ publisher: `published`
7. publisher → analyst: `metrics, insights` → CEO（次サイクルへ）

## 設計上の判断

- **疎結合**: エージェントは互いを直接呼ばず、n8nが仲介。1人を差し替えても他は無傷。
- **単一責務**: 1フォルダ1役割。テスト・改善・交換の単位が明確。
- **設定とコードの分離**: 秘密情報は環境変数、構造はYAML/JSONでGit管理。
