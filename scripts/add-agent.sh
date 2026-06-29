#!/usr/bin/env bash
# =============================================================
#  新しいAI社員をひな形から生成するCLI
#  使い方: ./scripts/add-agent.sh
# =============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$ROOT/agents/_template"
REGISTRY="$ROOT/config/agents.registry.yaml"

read -rp "社員ID (例: video-editor): " AGENT_ID
read -rp "表示名 (例: Video Editor): " AGENT_NAME
read -rp "役割 role (1つだけ, 例: video): " AGENT_ROLE
read -rp "上司 reportsTo [ceo]: " AGENT_BOSS
AGENT_BOSS="${AGENT_BOSS:-ceo}"

DEST="$ROOT/agents/$AGENT_ID"
if [ -d "$DEST" ]; then
  echo "❌ agents/$AGENT_ID は既に存在します。"; exit 1
fi

cp -r "$TEMPLATE" "$DEST"

# agent.config.json のプレースホルダを置換
sed -i.bak \
  -e "s/\"TEMPLATE\"/\"$AGENT_ID\"/" \
  -e "s/\"Template Agent\"/\"$AGENT_NAME\"/" \
  -e "s/\"REPLACE_ME\"/\"$AGENT_ROLE\"/" \
  -e "s/\"reportsTo\": \"ceo\"/\"reportsTo\": \"$AGENT_BOSS\"/" \
  "$DEST/agent.config.json"
rm -f "$DEST/agent.config.json.bak"

# 登録簿に追記
cat >> "$REGISTRY" <<YAML

  - id: $AGENT_ID
    name: $AGENT_NAME
    role: $AGENT_ROLE
    reportsTo: $AGENT_BOSS
    manual: agents/$AGENT_ID/manual.md
    prompt: agents/$AGENT_ID/prompt.md
    config: agents/$AGENT_ID/agent.config.json
    enabled: true
YAML

echo "✅ agents/$AGENT_ID を作成し、登録簿に追記しました。"
echo "   次に manual.md と prompt.md を編集してください。"
