# Addness AI社員 — スマホ / PC 共通運用（検証版）

## 共通の状態
- 司令塔: Addness。ゴール・実行記録・承認待ちをここで確認する。
- コード: このSNSリポジトリ。スマホとPCで別々の実行ロジックを作らない。
- 安全方針: 外部投稿・提携申請・課金は承認まで実行しない。
- 注意: 現在の `scripts/addness-task-loop.mjs` は**モック成果物の疎通検証用**。実案件の完了とは記録しない。

## スマホ（クラウド）
1. GitHubのActions画面で `Addness AI worker safety tests` を確認する。
2. PR上の変更時はオフラインテストが自動実行される。手動実行（workflow_dispatch）はGitHubの既定ブランチにワークフローが存在する必要がある。
3. この安全テストはAddnessの認証情報・AI APIキーを使わず、投稿・課金・書き戻しを行わない。
4. 本番クラウド実行は、認証方法と実行範囲を確認し、承認ゲートの動作を検証した後に別途有効化する。

## PC（Codex / ターミナル）
Node.js 22でリポジトリの対象ブランチをチェックアウトして以下を実行:

```bash
git checkout codex/addness-task-loop
node --test scripts/addness-task-loop.test.mjs
HUMAN_APPROVAL_REQUIRED=true node scripts/run-local.mjs --goal "安全な接続テスト"
```

PowerShellでは環境変数の設定が異なる:

```powershell
git checkout codex/addness-task-loop
node --test scripts/addness-task-loop.test.mjs
$env:HUMAN_APPROVAL_REQUIRED = "true"
node scripts/run-local.mjs --goal "安全な接続テスト"
```

## 実際のAddness接続を検証する際の条件
- `ADDNESS_SCOPE_GOAL_ID` で対象ゴールを限定する。
- CLIまたはMCPの認証を環境ごとに設定する。秘密情報をリポジトリにコミットしない。
- 最初は `node scripts/addness-task-loop.mjs --dry-run` で読み取り・選択を確認する。
- 書き戻し検証は、実際の投稿・成果を意味しない検証記録として区別する。
- 同時実行防止のため、本番運用前にクラウドとPCをまたぐ排他制御を追加・検証する。現状の日付・run_idによる重複検出だけでは競合を完全には防げない。

## 完了判定
- [ ] クラウドの安全テストが成功
- [ ] PCの安全テストが成功
- [ ] 実際のAddnessで1件取得→記録→読み戻し
- [ ] PCとクラウド間の同時実行・重複防止を検証
- [ ] モックではない業務成果物の生成を検証
- [ ] 承認なしの外部投稿・課金が不可能であることを検証
