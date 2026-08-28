# データモデル

すべての行は `company_id` から企業へ辿れます（企業単位のデータ分離）。
時刻はすべて UTC の ISO8601（秒精度）で保存し、表示時のみ企業のタイムゾーンへ変換します。

| テーブル | 役割 | 主なカラム |
|---|---|---|
| `companies` | 企業 | `name`, `hourly_cost_jpy`（削減コスト試算に使用） |
| `users` | ユーザー | `email`, `display_name`, `role`（member/manager/admin） |
| `devices` | PC | `hostname`, `os`, `agent_version`, `last_seen_at` |
| `consents` | 収集項目の同意 | `scope_key`, `enabled`, `granted_at`, `revoked_at` |
| `sessions` | 作業の開始・終了 | `started_at`, `ended_at`, `paused_sec` |
| `events` | 操作イベント | `event_type`, `scope_key`, `app_name`, `window_title`(マスク済), `url_domain`, `url_path_shape`, `file_ext`, `file_op`, `redacted` |
| `app_usage` | アプリ使用時間 | `app_name`, `app_category`, `duration_sec` |
| `analysis_runs` | 分析の実行単位 | `period_start/end`, `engine`, `status`, `stats_json` |
| `tasks` | 業務単位 | `name`, `summary`, `category`, `occurrence_count`, `avg_duration_sec`, `is_repetitive`, `repetition_score`, `confidence`, `inference_source` |
| `task_steps` | 業務の手順 | `seq`, `action`, `app_name`, `detail` |
| `task_occurrences` | 業務の実発生（根拠） | `started_at`, `ended_at`, `duration_sec`, `evidence` |
| `automation_candidates` | 自動化候補 | `rank`, `feasibility`, `method`, `difficulty`, `risk_level`, `human_judgment`, `frequency_per_month`, `est_saved_minutes_month`, `est_saved_cost_month_jpy`, `priority_score`, `step2_spec_json` |
| `candidate_decisions` | ユーザーの選択 | `decision`(automate/hold/exclude), `note`, `decided_by` |
| `audit_logs` | 監査ログ | `actor`, `action`, `target_type/id`, `detail` |
| `redaction_stats` | 破棄・マスクの件数 | `reason`, `scope_key`, `count`（内容は保存しない） |

## 収集スコープ一覧

| キー | 表示名 | 収集する | 収集しない |
|---|---|---|---|
| `app_usage` | 使用アプリケーションと使用時間 | アプリ名・種別・使用秒数 | 画面の中身、スクリーンショット |
| `window_title` | ウィンドウタイトル | マスク済みタイトル | 認証画面のタイトル、PII、本文 |
| `browser_usage` | Webブラウザの利用 | ドメイン、パス形状、滞在時間 | 完全なURL、クエリ、ページ本文、Cookie |
| `file_ops` | ファイル操作 | 操作種別、拡張子、時刻 | 中身、フルパス、ファイル名 |
| `input_activity` | 操作イベント（量のみ） | 入力回数、クリック回数 | 押下したキー、入力文字列、座標 |
| `clipboard_meta` | コピー＆ペーストの発生 | 操作種別、文字数の桁 | クリップボードの内容 |
| `work_hours` | 作業の開始・終了 | 開始/終了時刻、アイドル区間 | 業務時間外の私的利用の詳細 |
