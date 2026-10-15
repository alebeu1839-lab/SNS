#!/usr/bin/env node
/**
 * Run one Addness ToDo through the SNS pipeline in a no-post, no-billing dry run.
 *
 * Required: ADDNESS_MCP_URL (for example https://vt.api.addness.com/mcp)
 * Optional: ADDNESS_MCP_TOKEN (OAuth access token supplied by your MCP host).
 * This worker intentionally removes ANTHROPIC_API_KEY before running the pipeline.
 */
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const MCP_URL = process.env.ADDNESS_MCP_URL;
const TOKEN = process.env.ADDNESS_MCP_TOKEN;
const VERSION = '2025-03-26';

if (!MCP_URL) {
  console.error('ADDNESS_MCP_URL is required.');
  process.exit(2);
}

let requestId = 0;
let sessionId;
let protocolVersion = VERSION;

function parseRpc(text) {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try { return JSON.parse(trimmed); } catch {}
  const data = trimmed.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim());
  for (const item of data.reverse()) {
    try { return JSON.parse(item); } catch {}
  }
  throw new Error('MCP response was neither JSON nor a supported SSE event.');
}

async function rpc(method, params, notification = false) {
  const headers = {
    accept: 'application/json, text/event-stream',
    'content-type': 'application/json',
  };
  if (TOKEN) headers.authorization = `Bearer ${TOKEN}`;
  if (sessionId) headers['mcp-session-id'] = sessionId;
  if (protocolVersion) headers['mcp-protocol-version'] = protocolVersion;

  const body = { jsonrpc: '2.0', method, params };
  if (!notification) body.id = ++requestId;
  const response = await fetch(MCP_URL, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  const freshSession = response.headers.get('mcp-session-id');
  if (freshSession) sessionId = freshSession;
  if (response.status === 202 && notification) return null;
  const raw = await response.text();
  if (!response.ok) throw new Error(`MCP HTTP ${response.status}: ${raw.slice(0, 600)}`);
  const message = parseRpc(raw);
  if (message?.error) throw new Error(`MCP ${method}: ${message.error.message || JSON.stringify(message.error)}`);
  return message?.result;
}

function toolBySuffix(tools, suffix) {
  const found = tools.find((tool) => tool.name === suffix || tool.name.endsWith(`_${suffix}`));
  if (!found) throw new Error(`Addness MCP tool is unavailable: *${suffix}`);
  return found.name;
}

async function callTool(name, args) {
  const result = await rpc('tools/call', { name, arguments: args });
  if (result?.isError) {
    const details = result.content?.map((part) => part.text || '').join('\n');
    throw new Error(`Addness tool failed: ${details || name}`);
  }
  return result;
}

function unwrap(result) {
  if (result?.structuredContent) return result.structuredContent;
  for (const part of result?.content || []) {
    if (part.type !== 'text' || !part.text) continue;
    try { return JSON.parse(part.text); } catch { return part.text; }
  }
  return result;
}

function extractTodoList(value) {
  if (Array.isArray(value)) return value;
  for (const key of ['todos', 'items', 'tasks', 'data']) {
    if (Array.isArray(value?.[key])) return value[key];
  }
  return [];
}

function runDryPipeline(title) {
  const env = { ...process.env, HUMAN_APPROVAL_REQUIRED: 'true' };
  delete env.ANTHROPIC_API_KEY;
  delete env.DEFAULT_MODEL;
  const result = spawnSync(process.execPath, ['scripts/run-local.mjs', '--goal', title], {
    cwd: ROOT,
    env,
    encoding: 'utf8',
    timeout: 120_000,
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error((result.stderr || result.stdout || 'SNS dry run failed').slice(-2000));
  const output = result.stdout || '';
  if (!output.includes('MOCK (no API key)')) throw new Error('Dry-run safety check failed: expected mock mode.');
  if (!output.includes('投稿中止')) throw new Error('Dry-run safety check failed: expected publish to remain blocked.');
  return output;
}

async function main() {
  const initialized = await rpc('initialize', {
    protocolVersion: VERSION,
    capabilities: {},
    clientInfo: { name: 'sns-addness-task-loop', version: '0.1.0' },
  });
  protocolVersion = initialized?.protocolVersion || VERSION;
  await rpc('notifications/initialized', {}, true);

  const listed = await rpc('tools/list', {});
  const tools = listed?.tools || [];
  const listName = toolBySuffix(tools, 'list_today_todos');
  const getGoalName = toolBySuffix(tools, 'get_goal');
  const commentName = toolBySuffix(tools, 'add_comment');
  const listCommentsName = toolBySuffix(tools, 'list_comments');

  const queue = extractTodoList(unwrap(await callTool(listName, {})));
  const todo = queue.find((row) => row?.objectiveId || row?.objective_id);
  if (!todo) {
    console.log(JSON.stringify({ status: 'idle', reason: 'No goal-backed Addness ToDo to process.' }));
    return;
  }
  const goalId = todo.objectiveId || todo.objective_id;
  const todayJst = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());
  const marker = `## AI社員ループ 1件検証（${todayJst}）`;
  const existingComments = extractTodoList(unwrap(await callTool(listCommentsName, { goal_id: goalId, limit: 100 })));
  if (existingComments.some((item) => JSON.stringify(item).includes(marker))) {
    console.log(JSON.stringify({ status: 'already_processed', goal_id: goalId, date: todayJst }));
    return;
  }
  const goalResult = unwrap(await callTool(getGoalName, { goal_id: goalId }));
  const goalText = typeof goalResult === 'string' ? goalResult : JSON.stringify(goalResult);
  const title = goalText.match(/^#\s+\[[^\]]+\]\s+(.+)$/m)?.[1]?.trim() || 'Addness ToDo';
  const output = runDryPipeline(title);
  const proof = [
    marker,
    '',
    '- Addness ToDo取得: 成功（1件）',
    `- 対象ゴール: ${title}`,
    '- 業務実行: SNS既存パイプラインのモック実行に成功',
    '- 外部投稿: 実行なし（承認ゲートで停止）',
    '- API課金: なし（Anthropicキーを子プロセスから除外）',
    '- 結果書き戻し: このコメントに記録',
    '',
    '実行ログの要約:',
    output.split('\n').filter((line) => /mode:|MOCK|投稿中止|パイプライン完走/.test(line)).join('\n').slice(0, 3000),
  ].join('\n');

  await callTool(commentName, { goal_id: goalId, content: proof });
  console.log(JSON.stringify({
    status: 'success',
    todo_count: 1,
    goal_id: goalId,
    goal_title: title,
    external_post: false,
    billed_api: false,
    writeback: 'Addness goal comment',
  }, null, 2));
}

main().catch((error) => {
  console.error(JSON.stringify({ status: 'failed', message: error.message }, null, 2));
  process.exitCode = 1;
});
