#!/usr/bin/env node
// Minimal Codex -> Claude Code handoff. Requires Claude Code CLI login on the runner.
// No business system writes and no publishing.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

if (process.env.ANTHROPIC_API_KEY) {
  console.error(JSON.stringify({ok:false,reason:"ANTHROPIC_API_KEY set; API billing may apply"}));
  process.exit(2);
}
function resolveClaudeBinary() {
  if (process.env.CLAUDE_HANDOFF_TEST_BIN) return process.env.CLAUDE_HANDOFF_TEST_BIN;
  if (process.platform === "win32" && process.env.APPDATA) {
    const bundledExe = join(process.env.APPDATA, "npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe");
    if (existsSync(bundledExe)) return bundledExe;
  }
  return "claude";
}

const binary = resolveClaudeBinary();
const prefixArgs = process.env.CLAUDE_HANDOFF_TEST_PREFIX_ARGS
  ? JSON.parse(process.env.CLAUDE_HANDOFF_TEST_PREFIX_ARGS)
  : [];
if (!Array.isArray(prefixArgs) || !prefixArgs.every((arg) => typeof arg === "string")) {
  console.error(JSON.stringify({ok:false,reason:"invalid_test_prefix_args"}));
  process.exit(2);
}
const args = [...prefixArgs, "-p", "Reply only with CLAUDE_HANDOFF_OK. Do not use tools.", "--output-format", "text", "--max-turns", "1", "--tools", ""];
const result = spawnSync(binary, args, {
  encoding: "utf8", timeout: 120000, shell:false, env:process.env,
});
if (result.error) {
  console.error(JSON.stringify({ok:false,reason:"launch_failed",error:result.error.message}));
  process.exit(1);
}
const response = (result.stdout || "").trim();
const ok = result.status === 0 && response === "CLAUDE_HANDOFF_OK";
console.log(JSON.stringify({ok,exitCode:result.status,response,stderr:(result.stderr||"").slice(0,300)}));
process.exit(ok ? 0 : 1);
