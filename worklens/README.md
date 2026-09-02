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

手順の詳細は [docs/try-on-your-pc.md](docs/try-on-your-pc.md)。準備は1コマンドです。

```bash
python scripts/setup.py        # 依存の導入 + このPCで何が取得できるかの確認
```

```bash
# 0. このPCで何が収集できるかを、始める前に確認する
python -m worklens.agent.cli doctor

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

**実PCでの取得方法**（内容に触れずに「起きた事実」だけを取る）

| 何を | どうやって |
|---|---|
| 入力の有無 | OSの「最後の入力からの経過秒」のみ。**押されたキーは取得しない** |
| コピーの発生 | OSのクリップボード**変更カウンタ**のみ。**中身は一度も読まない** |
| 離席 | 同上。離席中は記録を止める（作業時間の水増しを防ぐ） |

貼り付け操作とファイル操作は実PCからは取得しません。転記業務は
「コピー → 別システムでの入力」の形で検出します。

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
python -m pytest -q      # 154 tests
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
| `test_agent_activity.py` | 離席・入力・コピーの検出、取得不可環境での縮退 |
| `test_step2_runner.py` | ガードレール（ドライラン・引き継ぎ・中断・ログ・削減時間） |
| `test_step2_transfer.py` | モック2システムを起動しての転記、二重登録防止、拒否の検出 |
| `test_step2_handoff.py` | STEP1の仕様がSTEP2の入力として使えること、実行記録の保存 |
| `test_step2_registry.py` | レシピ選択、未対応業務の拒否、接続先の解決（名前・ドメイン） |
| `test_step2_mail_to_sheet.py` | 項目抽出（LLM/ルール）、LLMに欠損を捏造させない、台帳書き込み |
| `test_step2_handoff_queue.py` | 引き継ぎの保存・重複防止・処理済み化、画面表示、企業分離 |
| `test_step2_more_recipes.py` | 見積書・返信・日報・監視の4レシピ。送信しないこと、所感を書かないこと、対象外と引き継ぎを混ぜないこと |

---

## STEP 2（自動化の実行）— 練習用の実装が入っています

STEP1 が第1位に挙げた「車両情報を社内管理システムから掲載サイトへ転記」を、
**練習用のモックシステム相手に実際に動かせます**。手順は
[docs/step2-tutorial.md](docs/step2-tutorial.md)（所要15分）。

```bash
pip install -r requirements-step2.txt && python -m playwright install chromium

python scripts/run_mocks.py                      # 練習用の3システムを起動
python -m worklens.step2.cli recipes             # 実装済みのレシピ
python -m worklens.step2.cli list                # 「自動化したい」候補を見る

# 第1位: Webシステム間の転記（ドライラン → 本番）
python -m worklens.step2.cli run --candidate <ID> \
  --map kanri.example.co.jp=http://127.0.0.1:9101 \
  --map keisai.example-portal.jp=http://127.0.0.1:9102
python -m worklens.step2.cli run --candidate <ID> --mode live --map ... --map ...

# 第2位: メールの内容を台帳へ入力
python -m worklens.step2.cli run --candidate <ID> --mode live \
  --map "Outlook=http://127.0.0.1:9103" --map "Excel=./顧客管理.xlsx"

# 第3位: 見積書PDF + メール下書き（送信はしない）
python -m worklens.step2.cli run --candidate <ID> --mode live \
  --map "Excel=./顧客管理.xlsx" --map "Outlook=http://127.0.0.1:9103" \
  --map "inventory=http://127.0.0.1:9101"

# 第4位: 返信の下書き / 第5位: 日報 / 第8位: 在庫の条件通知
python -m worklens.step2.cli run --candidate <ID> --mode live --map "mail=http://127.0.0.1:9103"
python -m worklens.step2.cli run --candidate <ID> --mode live --map "inventory=http://127.0.0.1:9101"
python -m worklens.step2.cli run --candidate <ID> --mode live \
  --map "inventory=http://127.0.0.1:9101" --map "notify=http://127.0.0.1:9104"

python -m worklens.step2.cli history             # 実行履歴と削減実績
```

実行結果と「人へ回った件」はダッシュボードの **/automation** から見られます。

### 実装済みのレシピ

| レシピ | 対応する業務種別 | 自動化する範囲と、止まる場所 |
|---|---|---|
| `web_transfer` | データ転記 | 参照元はAPIで取得、書き込み先だけブラウザ操作 |
| `mail_to_ledger` | データ入力 | メールから項目を抽出（Claude／ルール）し台帳へ追加。読めない件は人へ |
| `quote_and_draft` | 書類作成・送付 | 在庫と突き合わせ見積書PDFを生成し、**メールは下書きまで**。送信しない |
| `mail_reply_draft` | メール対応 | 分類して返信の**下書きまで**。送信しない。クレームは人へ |
| `daily_report` | 報告・レポート作成 | 数値を集計して日報を生成。**所感欄は空けて残す** |
| `inventory_monitor` | 確認・モニタリング | 全件を機械が確認し、**条件に触れたものだけ通知**する |

**自動化してよい範囲を切ることが、この4つの要点です。**

- 帳票の作成は機械の仕事だが、**送信は違う**。宛先や金額を間違えたメールは取り消せない。
  だから下書きで止め、送信ボタンは人が押す
- 日報の数値は機械の仕事だが、**所感は違う**。機械が書くと、読んだ人が
  「本当にこの人が書いたのか」を判断できなくなる
- 目視確認の削減は、速くすることではなく**やめること**で起きる。
  通知が来ない日は「見なくてよい」という情報になる

### 3つの結果を区別する

| | 意味 | 例 |
|---|---|---|
| 自動処理 | 機械が完了させた | 台帳へ登録、下書きを作成 |
| 人へ引き継ぎ | **判断が要る**ので人に回した | 予算超過、クレーム、複数台 |
| 対象外 | **そもそも用が無い** | 監視で異常なし、返信済み |

監視業務は大半が「対象外」です。これを引き継ぎに数えると未処理の山が毎日積み上がり、
誰も見なくなります。

**未対応の業務種別を指定すると、実行を拒否します。** 別のレシピを代用しません
（誤った業務をそれらしく自動実行するほうが害が大きいため）。

### 人へ回った件の扱い

自動処理しなかった件は `automation_handoffs` に残り、`/automation` に一覧で出ます。
**引き継ぎは失敗ではなく設計どおりの動作です。** 判断が要る仕事だけが人に残ります。
同じ対象が繰り返し引き継がれても未処理は1件に保たれ、対応後は画面から処理済みにできます。

STEP1 の `step2_spec_json`（トリガ・参照元/書き込み先・手順・ガードレール・未確認事項）
が、そのまま実行の入力になります。

### 実装されているガードレール

`step2_spec` の `guardrails` をコードにしたものです。

| ガードレール | 実装 |
|---|---|
| 実行ログを残し、経緯を人が追えるようにする | 1実行=1 JSONLファイル。全件の取得・判定・書き込みを記録 |
| ドライラン結果を人が確認してから本番適用 | 既定は `dry-run`。本番は `--mode live` の明示が必要 |
| 想定外パターンは自動処理を止めて人へ引き継ぐ | 判定に通らない件は書き込まず、理由付きで引き継ぎキューへ |

加えて、転記先ドメインは `--map` で明示しない限り解決されません
（検証環境から本番を叩く事故の防止）。書き込み失敗時は既定で実行を中断します。

```
worklens/step2/
  runner.py                    実行基盤（ログ・ドライラン・引き継ぎ・中断）。業務を知らない
  registry.py                  業務種別 → レシピの対応表。未対応なら実行を拒否する
  recipes/                     6レシピ。必要な接続先は各レシピが宣言する
  cli.py                       recipes / list / run / history
mock/                          在庫管理システム・掲載サイト・受信箱・通知先
```

レシピは `recipes/` に置くだけで自動的に登録されます（モジュールを列挙しません）。

新しい業務を自動化するときは `recipes/` にファイルを1つ足し、
`RecipeEntry` を登録するだけです。実行基盤は業務を知りません。

## まだレシピが無い業務

| 業務種別 | 理由 |
|---|---|
| 情報収集・調査 | 判断が中心。収集と要約は支援できるが、完全自動化には向かない |
| 会議・打合せ | 自動化対象外。分析側も「非推奨」と判定する |
| その他 | 手順が定まっておらず、レシピにできる形になっていない |

指定すると**実行を拒否します**。それらしく動いて誤った業務を実行するより、
「対応していない」と言うほうが安全だからです。

## STEP 3（運用・最適化）への拡張

- `automation_executions` に実行ごとの成功件数・引き継ぎ件数・削減分数が残ります。
- `automation_handoffs` に「自動化しても人に残った仕事」が積み上がります。
  同じ理由が繰り返し出るなら、そこが次の改善点です。
- `analysis_runs` は分析の実行単位で履歴が残るため、自動化前後の作業時間比較は
  同じテーブルの差分で行えます。

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
