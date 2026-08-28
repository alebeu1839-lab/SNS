# WorkLens — AI業務分析・自動化候補発見システム（STEP 1）

企業のPC業務を分析し、**自動化する価値の高い業務を発見して提案する**システムです。
STEP 1 では「業務データ収集 → 業務分析 → 自動化候補の抽出 → レポート表示 → ユーザーの選択保存」
までを実装しています。自動化の実装（STEP 2）は含みません。

このシステムはPC監視ソフトではありません。目的は
「Chromeを30分使いました」ではなく
**「中古車情報を管理システムからコピーし、掲載サイトへ入力する作業を1日12回実施している」**
という、業務として意味のある単位を発見することです。

---

## 5分で動かす

```bash
cd worklens
pip install -r requirements.txt

# 企業・ユーザー・PC登録 → 合成ワークロード収集 → 分析まで一括実行
python scripts/run_demo.py --reset --days 20

# ダッシュボードを開く
python -m uvicorn worklens.api.app:app --port 8000
#   → http://localhost:8000
```

`ANTHROPIC_API_KEY` を設定すると業務名・説明の推定に Claude を使います。
**未設定でも決定論的なルールエンジンにフォールバックして全機能が動作します。**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export WORKLENS_MODEL=claude-sonnet-5      # 任意
```

---

## 実PCで使う

```bash
# 1. 初期化（企業 / ユーザー / PC を登録し、収集項目の既定値を設定）
python -m worklens.agent.cli init \
  --company "サンプル自動車販売" --name "山田太郎" --email yamada@example.co.jp

# 2. 何を収集するかを確認する
python -m worklens.agent.cli scopes

# 3. 収集項目を個別にOFFにする
python -m worklens.agent.cli consent set browser_usage off

# 4. 収集する（30分間）
python -m worklens.agent.cli collect --source live --minutes 30

# 5. 分析して自動化候補を作る
python -m worklens.agent.cli analyze --period-days 30

# 6. 状態確認 / データ削除
python -m worklens.agent.cli status
python -m worklens.agent.cli purge --scope user
```

実PC収集にはOS別の追加ライブラリが必要です。未導入でもエラーにはならず、
必要なコマンドを案内して終了します。

| OS | 追加依存 |
|---|---|
| Windows | `pip install -r requirements-agent-windows.txt`（pywin32 / psutil） |
| macOS | `pip install -r requirements-agent-macos.txt`（pyobjc。ウィンドウタイトルには「画面収録」許可が必要） |
| Linux | `sudo apt install xdotool`（X11セッションのみ） |

---

## システム構成

```
① PCエージェント          worklens/agent/
     collectors/          OS別コレクタ + 合成ワークロード生成
     scopes.py            収集項目の定義（= 画面に出す説明の唯一の情報源）
     privacy.py           機密情報の除外・マスク（DBへ入る前に適用）
     recorder.py          セッション管理・取り込み・収集停止
     cli.py               init / scopes / consent / collect / analyze /
                          status / purge

② データ保存              worklens/storage/
     schema.sql           企業/ユーザー/PC/セッション/イベント/アプリ使用/
                          業務/自動化候補/選択結果/監査ログ を分離
     repositories.py      SQLを閉じ込めたリポジトリ層

③ 業務分析AI              worklens/analysis/
     sessionizer.py       生イベント → 作業セグメント
     pattern.py           作業ブロック分割 + 繰り返しパターン検出
     task_inference.py    セグメント列 → 業務単位（Claude + ルールベース）

④ 自動化候補分析AI        worklens/analysis/automation.py
                          可能性 / 方法 / 頻度 / 作業時間 / 削減時間 /
                          難易度 / リスク / 人の判断 を評価してランキング

⑤ ダッシュボード          worklens/api/ + worklens/web/
⑥ ユーザーによる選択      candidate_decisions テーブルへ保存（STEP1は保存まで）
```

### 分析の流れ

```
操作イベント (6,500件)
   │  Sessionizer          … 同じ作業文脈に留まっていた区間へまとめる
   ▼
作業セグメント (909区間)   例: 社内管理システム|コピー (48秒)
   │  pattern.split_blocks … 「間（ま）」の分布から切れ目を自動推定（Otsu法）
   ▼
作業ブロック (412個)       ≒ 業務の1回分
   │  pattern.mine         … 手順シグネチャでクラスタリング + 部分列マイニング
   ▼
繰り返しクラスタ (18種)    例: [管理システム|コピー → 掲載サイト|貼り付け] × 182回
   │  task_inference       … 人が読める業務名・説明・手順へ変換
   ▼
業務単位 (16件)            例: 車両情報を社内管理システムから掲載サイトへ転記
   │  automation           … 自動化可能性・削減時間・難易度・リスクを評価
   ▼
自動化候補 (16件、ランキング済み)
```

### 削減時間の算出式

計測値から決定論的に算出します（LLMに数字を作らせません）。

```
月間発生回数 = 観測回数 / 観測稼働日数 × 月間営業日数(20日)
現状作業時間 = 月間発生回数 × 1回あたり平均所要時間
推定削減時間 = 現状作業時間 × 自動化可能性 × (1 - 人の判断が残る割合)
                            × (1 - 運用オーバーヘッド)
推定削減コスト = 推定削減時間 × 企業の平均人件費(円/時)
優先順位スコア = 推定削減時間 × 自動化可能性 / (1 + 難易度スコア)
```

---

## セキュリティ・プライバシー

| 要件 | 実装 |
|---|---|
| 明示的なユーザー同意 | `consents` テーブル。同意していないスコープのイベントは**保存前に破棄** |
| データ収集項目の設定 | 7つの収集スコープを個別にON/OFF（CLI・画面の双方から） |
| 収集停止ボタン | ダッシュボードの「すべての収集を停止する」／`consent all-off` |
| データ削除機能 | ユーザー単位・企業単位の削除（企業単位は admin のみ） |
| 企業単位のデータ分離 | 全テーブルに `company_id`。取得系はすべて企業でフィルタ |
| アクセス権限管理 | member / manager / admin。ログ閲覧は manager 以上、全社削除は admin |
| 機密情報の除外 | パスワードマネージャ・認証画面・銀行サイトはイベントごと破棄 |
| ログ管理 | `audit_logs` に同意変更・収集停止・分析実行・選択操作を記録 |
| 透明性 | 「収集データの確認」画面で、収集項目・保存済みデータ・破棄件数を提示 |

**構造的に収集できないもの**（フィールド自体が存在しない）

- 押されたキーの内容（入力は「回数」のみ）
- クリップボードの中身（「コピー/貼り付けが起きた事実」と文字数の桁のみ）
- メール本文・チャット本文（本文と判定した文字列は保存せず置換）
- 完全なURL・クエリ文字列（ドメインとパス形状のみ。IDは `:id` へ正規化）
- ファイルのフルパス・ファイル名（拡張子と操作種別のみ）
- スクリーンショット

---

## テスト

```bash
pip install -r requirements-dev.txt
python -m pytest -q      # 68 tests
```

| ファイル | 検証内容 |
|---|---|
| `test_privacy.py` | 機密アプリ/画面/ドメインの破棄、PIIマスク、本文非保存、URL正規化 |
| `test_storage.py` | 同意の取消記録、企業間のデータ分離、削除、選択のUPSERT、UTC正規化 |
| `test_analysis.py` | セグメント化、区切りの自動推定、周期シグネチャの畳み込み、業務命名規則 |
| `test_automation.py` | 可能性スコアの妥当性、ランキング順序、削減時間・コストの算出 |
| `test_pipeline_e2e.py` | 収集→分析→候補までの通し、収集停止時に何も残らないこと |
| `test_api.py` | 全画面の描画、必須項目の表示、選択の保存/取消、権限、企業分離 |
| `test_agent_cli.py` | init / scopes / consent / collect / status |

---

## STEP 2・STEP 3 への拡張

- 各自動化候補は `step2_spec_json` に**実装仕様の下書き**（トリガ・対象システム・
  手順・ガードレール・未確認事項）を自動生成済みです。STEP 2 はこれを入力に使えます。
- `candidate_decisions` に「自動化したい」と記録された候補が STEP 2 の実装キューになります。
- `analysis_runs` は実行単位で履歴が残るため、STEP 3 の効果測定
  （自動化前後の作業時間比較）は同じテーブルの差分で行えます。

---

## 主な環境変数

| 変数 | 既定値 | 説明 |
|---|---|---|
| `WORKLENS_HOME` | `~/.worklens` | DBとプロファイルの保存先 |
| `WORKLENS_DB` | `$WORKLENS_HOME/worklens.db` | SQLiteのパス |
| `ANTHROPIC_API_KEY` | （未設定） | 設定時のみ Claude を使用 |
| `WORKLENS_MODEL` | `claude-sonnet-5` | 使用モデル |
| `WORKLENS_TZ` | `Asia/Tokyo` | 稼働日数の集計と画面表示のタイムゾーン |
| `WORKLENS_IDLE_GAP` | `180` | 離席とみなす秒数 |
| `WORKLENS_MIN_REPETITION` | `3` | 繰り返しと判定する最小出現回数 |
