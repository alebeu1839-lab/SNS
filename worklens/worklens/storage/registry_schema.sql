-- =====================================================================
--  登録制の汎用データモデル（業種・会社・部署・担当者・ツール・業務）
--
--  設計の要点
--   1. 業種も使用ソフトも「登録するもの」。特定業種を前提にしない。
--      中古車販売店のサンプルは samples/ に登録データとして置き、
--      コードには一切埋め込まない。
--   2. 数値は必ず出所（実測 / 推定 / 申告）とセットで持つ。
--      process_metrics に集約し、出所の無い数値を作れないようにする。
--   3. 既存テーブル（companies / users）は壊さず、足りない列を追加する。
-- =====================================================================

-- ------------------------------------------------------------- 業種
CREATE TABLE IF NOT EXISTS industries (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    note       TEXT,
    created_at TEXT NOT NULL
);

-- ------------------------------------------------------------- 部署
CREATE TABLE IF NOT EXISTS departments (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (company_id, name)
);
CREATE INDEX IF NOT EXISTS idx_dept_company ON departments(company_id);

-- --------------------------------------------------------- 担当者
CREATE TABLE IF NOT EXISTS staff (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    department_id TEXT REFERENCES departments(id) ON DELETE SET NULL,
    name          TEXT NOT NULL,
    job_title     TEXT,
    email         TEXT,
    -- 人件費は担当者ごとに違う。未設定なら会社の既定値を使う。
    hourly_cost_jpy INTEGER,
    created_at    TEXT NOT NULL,
    UNIQUE (company_id, name, department_id)
);
CREATE INDEX IF NOT EXISTS idx_staff_company ON staff(company_id);

-- ------------------------------------------------------------ 使用PC
CREATE TABLE IF NOT EXISTS workplaces (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    os         TEXT,
    note       TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (company_id, name)
);

-- ----------------------------------------- 使用ソフト / Webサービス
-- kind: software（インストール型）/ web_service（SaaS）/ system（社内システム）
CREATE TABLE IF NOT EXISTS tools (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'software',
    vendor      TEXT,
    -- 自動化方法の選定に効く。API が有るかどうかで RPA か API 連携かが変わる。
    has_api     INTEGER NOT NULL DEFAULT 0,      -- 0=無/1=有/2=不明
    has_csv_io  INTEGER NOT NULL DEFAULT 0,
    url_domain  TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (company_id, name)
);
CREATE INDEX IF NOT EXISTS idx_tools_company ON tools(company_id);

-- ------------------------------------------------------------- 業務
CREATE TABLE IF NOT EXISTS processes (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    department_id  TEXT REFERENCES departments(id) ON DELETE SET NULL,
    owner_staff_id TEXT REFERENCES staff(id) ON DELETE SET NULL,
    name           TEXT NOT NULL,
    summary        TEXT,
    category       TEXT,
    -- 分析に効く定性フラグ（登録時にヒアリングで埋める）
    has_data_entry    INTEGER NOT NULL DEFAULT 0,   -- 入力作業がある
    has_transcription INTEGER NOT NULL DEFAULT 0,   -- 転記がある
    has_judgment      INTEGER NOT NULL DEFAULT 0,   -- 判断作業がある
    judgment_note     TEXT,                          -- どんな判断か
    is_person_dependent INTEGER NOT NULL DEFAULT 0, -- 属人化している
    security_level    TEXT NOT NULL DEFAULT 'normal', -- low/normal/high
    -- この業務データがどこから来たか（manual/csv/json/sample/task_mining）
    origin         TEXT NOT NULL DEFAULT 'manual',
    origin_note    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_process_company ON processes(company_id);

-- ------------------------------------------- 業務の数値（出所つき）
-- rule: 数値は必ず source を伴う。ここを通さずに数値を持たせない。
-- source: measured（実測）/ estimated（推定）/ declared（申告・ヒアリング）
CREATE TABLE IF NOT EXISTS process_metrics (
    id          TEXT PRIMARY KEY,
    process_id  TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    metric_key  TEXT NOT NULL,
    value_num   REAL NOT NULL,
    unit        TEXT,
    source      TEXT NOT NULL,
    evidence    TEXT,                 -- 実測なら根拠、推定なら前提
    recorded_at TEXT NOT NULL,
    UNIQUE (process_id, metric_key)
);
CREATE INDEX IF NOT EXISTS idx_metric_process ON process_metrics(process_id);

-- ------------------------------------------------- 業務が使うツール
CREATE TABLE IF NOT EXISTS process_tools (
    id         TEXT PRIMARY KEY,
    process_id TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    tool_id    TEXT NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    role       TEXT,                  -- 参照元 / 入力先 / 作成 / 送信 など
    UNIQUE (process_id, tool_id, role)
);

-- ------------------------------------------------------- 作業ステップ
CREATE TABLE IF NOT EXISTS process_steps (
    id          TEXT PRIMARY KEY,
    process_id  TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    action      TEXT NOT NULL,
    tool_id     TEXT REFERENCES tools(id) ON DELETE SET NULL,
    input_info  TEXT,
    output_info TEXT,
    note        TEXT,
    UNIQUE (process_id, seq)
);

-- ------------------------------------------------- 問題点・エラー
-- kind: error（起きるミス）/ attribution（属人化）/ problem（その他の困りごと）
CREATE TABLE IF NOT EXISTS process_issues (
    id          TEXT PRIMARY KEY,
    process_id  TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issue_process ON process_issues(process_id);

-- --------------------------------------------------- 取り込みの記録
CREATE TABLE IF NOT EXISTS import_runs (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,        -- csv / json / manual / sample
    filename    TEXT,
    created     INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_import_company ON import_runs(company_id, created_at);
