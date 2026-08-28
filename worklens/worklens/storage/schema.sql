-- =====================================================================
-- WorkLens STEP1 スキーマ
--   SaaS化を見据え、企業 / ユーザー / PC / セッション / 操作イベント /
--   アプリ使用 / 業務 / 自動化候補 を完全に分離したテーブル構成にする。
--   すべての行は company_id へ辿れる（企業単位のデータ分離のため）。
--   ID は TEXT(UUID) 固定。Postgres へ移行しても型が変わらない。
-- =====================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- 企業
CREATE TABLE IF NOT EXISTS companies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    -- 削減コスト試算に使う平均人件費（円/時）
    hourly_cost_jpy INTEGER NOT NULL DEFAULT 3500,
    created_at      TEXT NOT NULL
);

-- ------------------------------------------------------------ ユーザー
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    display_name TEXT NOT NULL,
    department   TEXT,
    -- アクセス権限管理: member / manager / admin
    role         TEXT NOT NULL DEFAULT 'member',
    created_at   TEXT NOT NULL,
    UNIQUE (company_id, email)
);
CREATE INDEX IF NOT EXISTS idx_users_company ON users(company_id);

-- ------------------------------------------------------------------ PC
CREATE TABLE IF NOT EXISTS devices (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hostname      TEXT NOT NULL,
    os            TEXT NOT NULL,
    os_version    TEXT,
    agent_version TEXT NOT NULL,
    timezone      TEXT NOT NULL DEFAULT 'Asia/Tokyo',
    registered_at TEXT NOT NULL,
    last_seen_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);

-- ------------------------------------------------------- 同意 / 収集設定
-- 収集項目ごとに ON/OFF。OFF のスコープはエージェント側で保存前に破棄する。
CREATE TABLE IF NOT EXISTS consents (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id   TEXT REFERENCES devices(id) ON DELETE CASCADE,
    scope_key   TEXT NOT NULL,          -- collection_scopes.py の ScopeKey
    enabled     INTEGER NOT NULL DEFAULT 0,
    granted_at  TEXT,
    revoked_at  TEXT,
    updated_at  TEXT NOT NULL,
    UNIQUE (user_id, device_id, scope_key)
);
CREATE INDEX IF NOT EXISTS idx_consents_user ON consents(user_id);

-- ---------------------------------------------------------- セッション
-- 「作業の開始・終了」を表す単位。エージェント起動〜停止、または日跨ぎで区切る。
CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id      TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    started_at     TEXT NOT NULL,
    ended_at       TEXT,
    -- 収集が一時停止されていた秒数（収集停止ボタン）
    paused_sec     INTEGER NOT NULL DEFAULT 0,
    agent_version  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_start ON sessions(user_id, started_at);

-- -------------------------------------------------------- 操作イベント
-- 機密情報はここに到達する前に privacy.Redactor で除去・マスクされる。
CREATE TABLE IF NOT EXISTS events (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    session_id     TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts             TEXT NOT NULL,
    -- app_focus / window_title / browser_navigate / file_op / clipboard_op
    -- / input_burst / idle_start / idle_end
    event_type     TEXT NOT NULL,
    scope_key      TEXT NOT NULL,       -- どの収集スコープで取得したか（監査用）
    app_name       TEXT,
    app_category   TEXT,                -- spreadsheet / mail / browser / ...
    window_title   TEXT,                -- マスク済み
    url_domain     TEXT,                -- ドメインのみ。クエリ・パスは保存しない
    url_path_shape TEXT,                -- 例 /cars/:id （IDは正規化）
    file_ext       TEXT,
    file_op        TEXT,                -- create / save / rename / move / delete
    detail         TEXT,                -- 構造化された補足（JSON文字列）
    redacted       INTEGER NOT NULL DEFAULT 0,  -- マスク処理が入ったか
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session_ts ON events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_company_ts ON events(company_id, ts);

-- ---------------------------------------------------------- アプリ使用
CREATE TABLE IF NOT EXISTS app_usage (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    app_name     TEXT NOT NULL,
    app_category TEXT,
    started_at   TEXT NOT NULL,
    ended_at     TEXT NOT NULL,
    duration_sec INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_usage_session ON app_usage(session_id);

-- ---------------------------------------------------- 分析ラン（実行単位）
CREATE TABLE IF NOT EXISTS analysis_runs (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id       TEXT REFERENCES users(id) ON DELETE CASCADE,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    engine        TEXT NOT NULL,        -- llm:claude-... / rule-based
    status        TEXT NOT NULL,        -- running / succeeded / failed
    stats_json    TEXT,
    error         TEXT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_company ON analysis_runs(company_id, started_at);

-- ---------------------------------------------------------------- 業務
-- 操作ログではなく「人間が理解できる業務単位」。
CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_id            TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,    -- 例: 中古車情報を管理システムから掲載サイトへ転記
    summary           TEXT NOT NULL,    -- 現在の作業内容（人間向け説明）
    category          TEXT NOT NULL,    -- データ転記 / 情報確認 / 資料作成 / ...
    apps_json         TEXT NOT NULL,    -- 使用している業務ツール
    occurrence_count  INTEGER NOT NULL DEFAULT 0,
    avg_duration_sec  INTEGER NOT NULL DEFAULT 0,
    total_duration_sec INTEGER NOT NULL DEFAULT 0,
    per_day_count     REAL NOT NULL DEFAULT 0,
    is_repetitive     INTEGER NOT NULL DEFAULT 0,
    repetition_score  REAL NOT NULL DEFAULT 0,   -- 0..1
    confidence        REAL NOT NULL DEFAULT 0,   -- 推定の確信度 0..1
    inference_source  TEXT NOT NULL,             -- llm / rule
    first_seen_at     TEXT,
    last_seen_at      TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id);

-- 業務の手順（「Excelで顧客情報を入力」等の粒度）
CREATE TABLE IF NOT EXISTS task_steps (
    id        TEXT PRIMARY KEY,
    task_id   TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq       INTEGER NOT NULL,
    action    TEXT NOT NULL,   -- 開く / 参照する / コピーする / 入力する / 保存する
    app_name  TEXT,
    detail    TEXT NOT NULL,
    UNIQUE (task_id, seq)
);

-- 業務の実発生（根拠となるセグメント）
CREATE TABLE IF NOT EXISTS task_occurrences (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    started_at   TEXT NOT NULL,
    ended_at     TEXT NOT NULL,
    duration_sec INTEGER NOT NULL,
    evidence     TEXT              -- 根拠イベントIDのJSON配列
);
CREATE INDEX IF NOT EXISTS idx_occ_task ON task_occurrences(task_id);

-- -------------------------------------------------------- 自動化候補
CREATE TABLE IF NOT EXISTS automation_candidates (
    id                        TEXT PRIMARY KEY,
    company_id                TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    task_id                   TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    run_id                    TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    rank                      INTEGER NOT NULL,
    feasibility               REAL NOT NULL,        -- 自動化可能性 0..1
    method                    TEXT NOT NULL,        -- RPA / API連携 / AI(LLM) / スクリプト / ハイブリッド
    method_detail             TEXT NOT NULL,        -- 推奨する自動化方法の説明
    tools_json                TEXT NOT NULL,        -- 想定ツール
    difficulty                TEXT NOT NULL,        -- 低 / 中 / 高
    difficulty_score          REAL NOT NULL,        -- 0..1（高いほど難しい）
    risk_level                TEXT NOT NULL,        -- 低 / 中 / 高
    risk_notes                TEXT NOT NULL,
    human_judgment            TEXT NOT NULL,        -- 不要 / 一部必要 / 必須
    human_judgment_ratio      REAL NOT NULL,        -- 0..1
    frequency_per_month       REAL NOT NULL,        -- 作業頻度（月）
    est_minutes_per_run       REAL NOT NULL,        -- 推定作業時間（1回）
    est_current_minutes_month REAL NOT NULL,        -- 月間の現状作業時間
    est_saved_minutes_month   REAL NOT NULL,        -- 推定削減時間（月）
    est_saved_cost_month_jpy  INTEGER NOT NULL,     -- 推定削減コスト（月）
    priority_score            REAL NOT NULL,
    rationale                 TEXT NOT NULL,
    -- STEP2 の実装フェーズへ渡すための入力仕様（今は生成のみ）
    step2_spec_json           TEXT,
    created_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cand_run_rank ON automation_candidates(run_id, rank);

-- ------------------------------------------------ ユーザーによる選択結果
CREATE TABLE IF NOT EXISTS candidate_decisions (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL REFERENCES automation_candidates(id) ON DELETE CASCADE,
    task_id      TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    decided_by   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- automate（自動化したい） / hold（今回は保留） / exclude（対象外）
    decision     TEXT NOT NULL,
    note         TEXT,
    decided_at   TEXT NOT NULL,
    UNIQUE (candidate_id)
);

-- -------------------------------------------------------- 監査ログ
CREATE TABLE IF NOT EXISTS audit_logs (
    id          TEXT PRIMARY KEY,
    company_id  TEXT,
    actor       TEXT NOT NULL,     -- user:<id> / agent:<device_id> / system
    action      TEXT NOT NULL,
    target_type TEXT,
    target_id   TEXT,
    detail      TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_company ON audit_logs(company_id, created_at);

-- ------------------------------------- 収集の透明性（何を落としたかの記録）
-- 本文などは保存せず「除外した件数と理由」だけを残す。
CREATE TABLE IF NOT EXISTS redaction_stats (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    session_id  TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    reason      TEXT NOT NULL,     -- scope_disabled / sensitive_app / pii_masked / ...
    scope_key   TEXT,
    count       INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    UNIQUE (session_id, reason, scope_key)
);

-- ============================ STEP2: 自動化の実行記録 ============================
-- STEP1 が出した候補に対して、実際に自動化を走らせた結果を残す。
-- ドライランと本番を同じ形で記録し、STEP3 の効果測定の元データにする。
CREATE TABLE IF NOT EXISTS automation_executions (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    candidate_id   TEXT NOT NULL REFERENCES automation_candidates(id) ON DELETE CASCADE,
    task_id        TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    recipe         TEXT NOT NULL,
    mode           TEXT NOT NULL,     -- dry-run / live
    status         TEXT NOT NULL,     -- succeeded / partial / failed
    processed      INTEGER NOT NULL DEFAULT 0,
    succeeded      INTEGER NOT NULL DEFAULT 0,
    handoff        INTEGER NOT NULL DEFAULT 0,   -- 人へ引き継いだ件数
    failed         INTEGER NOT NULL DEFAULT 0,
    saved_minutes  REAL NOT NULL DEFAULT 0,
    log_path       TEXT,
    detail_json    TEXT,
    error          TEXT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_exec_candidate
    ON automation_executions(candidate_id, started_at);
