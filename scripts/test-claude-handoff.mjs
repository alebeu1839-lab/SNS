#!/usr/bin/env node
// Smoke test: Codex (or a human) invokes Claude Code and receives a response.
// Does not modify files, call external business services, or publish posts.
import { spawnSync } from "node:child_process";

const prompt = "Reply only with the exact token CLAUDE_HANDOFF_OK. Do not use tools.";
if (process.env.ANTHROPIC_API_KEY) {
  console.error("ANTHROPIC_API_KEY is set. Claude Code may use usage-based API billing. Unset it if you want subscription authentication.");
  process.exit(2);
}
const result = spawnSync("claude", ["-p", prompt, "--output-format", "text", "--max-turns", "1", "--tools", ""], {
  encoding: "utf8",
  timeout: 60000,
  shell: false,
  env: process.env,
});
if (result.error) {
  console.error(JSON.stringify({ ok: false, step: "launch", error: result.error.message }));
  process.exit(1);
}
const response = (result.stdout || "").trim();
const ok = result.status === 0 && response === "CLAUDE_HANDOFF_OK";
console.log(JSON.stringify({ ok, exitCode: result.status, response, stderr: (result.stderr || "").slice(0, 500) }, null, 2));
process.exit(ok ? 0 : 1);
