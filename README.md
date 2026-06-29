# AI SNS Company 🏢🤖

**1人で運営できる「AIだけのSNS運用会社」** のソースコード・運用基盤です。

役割ごとに分離したAIエージェント（= AI社員）が、CEOエージェントの指示のもとで
SNS運用（企画 → 制作 → 投稿 → 分析 → 顧客対応）を自律的に回します。

---

## 設計思想（3つの原則）

1. **1エージェント = 1役割** — `agents/<role>/` の1フォルダが1人のAI社員。責務を混ぜない。
2. **CEOが司令塔** — `agents/ceo/` が全社員へタスクを分解・振り分けるオーケストレーター。
3. **レジストリ駆動の拡張性** — `config/agents.registry.yaml` に登録するだけでAI社員を増やせる。

```
人間（オーナー）
      │ ゴールを与える
      ▼
   [CEO Agent] ── タスク分解・振り分け
      ├─▶ content-planner   企画
      ├─▶ copywriter        本文作成
      ├─▶ designer          ビジュアル指示
      ├─▶ scheduler         投稿予約
      ├─▶ publisher         投稿実行
      ├─▶ analyst           分析・レポート
      └─▶ community-manager コメント/DM対応
```

---

## ディレクトリ構成

| パス | 役割 |
|---|---|
| `agents/` | AI社員（1フォルダ=1社員=1役割） |
| `agents/_template/` | 新社員のひな形。コピーして増やす |
| `agents/ceo/` | 司令塔。全社員に指示を出す |
| `core/` | 全社員共通の基盤（共通プロンプト・I/Oスキーマ） |
| `workflows/` | n8n ワークフロー（JSON、自動実行の本体） |
| `config/` | 社員登録簿・SNSチャンネル定義 |
| `docs/` | アーキテクチャ・組織図・社員追加手順 |
| `scripts/` | 運用CLI（社員追加など） |
| `.github/` | CI（マニュアル/レジストリ整合性チェック） |

---

## クイックスタート

```bash
# 1. 環境変数を準備
cp .env.example .env
#   → .env を編集してAPIキー等を設定

# 2. AI社員の一覧を確認
cat config/agents.registry.yaml

# 3. ローカルで動作確認（n8n不要・キー無しならモックで動く）
node scripts/run-local.mjs
#   → CEO→企画→本文→デザイン→予約→投稿→分析 を1回流して連結を検証
#   → ANTHROPIC_API_KEY を設定すれば同じ流れを実モデルで実行:
#       ANTHROPIC_API_KEY=sk-ant-... node scripts/run-local.mjs
#   → ライブで送るリクエストをキー無しで確認（経路検証）:
#       node scripts/run-local.mjs --show-request copywriter

# 4. SNSアカウントを繋いで運用開始（手順は docs/go-live.md）
docker compose up -d                                  # n8n 起動
node --env-file=.env scripts/check-connections.mjs    # 接続チェック(preflight)

# 5. 新しいAI社員を増やす
./scripts/add-agent.sh
```

---

## ドキュメント

- [運用開始ガイド（SNSを繋いで稼働）](docs/go-live.md)
- [アーキテクチャ全体図](docs/architecture.md)
- [組織図](docs/org-chart.md)
- [新AI社員の追加手順](docs/onboarding.md)

---

## 技術スタック

- **オーケストレーション / 自動実行**: [n8n](https://n8n.io/)
- **AIモデル**: Claude（Anthropic API）等、エージェントごとに `agent.config.json` で指定
- **構成管理**: GitHub（このリポジトリ）
- **シークレット管理**: 環境変数（`.env` / n8n Credentials）
