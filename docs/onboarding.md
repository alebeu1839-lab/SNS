# 新しいAI社員を追加する手順

このシステムは「**フォルダ追加＋登録簿に1行**」で社員を増やせるよう設計されています。
1エージェント=1役割の原則を必ず守ってください。

## 手順（手動）

1. **テンプレをコピー**
   ```bash
   cp -r agents/_template agents/<new-agent-id>
   ```

2. **マニュアルを書く** — `agents/<new-agent-id>/manual.md`
   - 役割を1文で（複数の責務を持たせない）
   - 入力・出力・SOP・禁止事項を埋める

3. **システムプロンプトを書く** — `prompt.md`

4. **設定を埋める** — `agent.config.json`
   - `id`, `role`, `reportsTo`（通常 `ceo`）, `model`, `tools`

5. **登録簿に追記** — `config/agents.registry.yaml` に1ブロック追加
   ```yaml
   - id: <new-agent-id>
     name: <表示名>
     role: <唯一の役割>
     reportsTo: ceo
     manual: agents/<new-agent-id>/manual.md
     prompt: agents/<new-agent-id>/prompt.md
     config: agents/<new-agent-id>/agent.config.json
     enabled: true
   ```

6. **n8nに組み込む** — `workflows/` のパイプラインにAIノードを追加し、
   前後のエージェントとhandoffで接続する。

7. **CIで検証** — push すると `.github/workflows/ci.yml` が
   レジストリと実ファイルの整合性をチェックする。

## 手順（CLI）

```bash
./scripts/add-agent.sh   # 対話形式でフォルダとひな形を生成
```

## チェックリスト

- [ ] 役割は1つだけか？
- [ ] 既存社員と役割が重複していないか？
- [ ] `reportsTo` は `ceo` か？
- [ ] レジストリに登録したか？
- [ ] CEOのprompt.mlで触れるべき新パイプラインはないか？
