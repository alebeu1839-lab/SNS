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
import { buildPipelineEnv, findRunMemory, getRunId, getTaskIdentity, selectUniqueGoalTodo, validateMcpUrl, verifyWriteback } from './addness-task-loop-utils.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const MCP_URL_RAW = process.env.ADDNESS_MCP_URL;
const TOKEN = process.env.ADDNESS_MCP_TOKEN;
const VERSION = '2025-03-26';

if (!MCP_URL_RAW) {
  console.error('ADDNESS_MCP_URL is required.');
  process.exit(2);
}
const MCP_URL = validateMcpUrl(MCP_URL_RAW);

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
  const env = buildPipelineEnv(process.env);
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
  const appendMemoryName = toolBySuffix(tools, 'append_goal_memory');
  const listMemoriesName = toolBySuffix(tools, 'list_goal_memories');

  const queue = extractTodoList(unwrap(await callTool(listName, {})));
  const selection = selectUniqueGoalTodo(queue);
  if (selection.status === 'idle') {
    console.log(JSON.stringify({ status: 'idle', reason: 'No goal-backed Addness ToDo to process.' }));
    return;
  }
  if (selection.status === 'needs_priority_decision') {
    console.log(JSON.stringify({ status: 'needs_priority_decision', goal_backed_todo_count: selection.count, reason: 'Addness does not expose an explicit ordering contract; no task was selected.' }));
    return;
  }
  const todo = selection.todo;
  const goalId = todo.objectiveId || todo.objective_id;
  const todayJst = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());
  const taskIdentity = getTaskIdentity(todo);
  const runId = getRunId({ date: todayJst, goalId, taskIdentity });
  const marker = `AI社員ループのモック検証（${todayJst}）\nrun_id: ${runId}\ngoal_id: ${goalId}\ntask_identity: ${taskIdentity}`;
  if (await findRunMemory({ goalId, runId, listMemoriesName, callTool, unwrap })) {
    console.log(JSON.stringify({ status: 'already_processed', goal_id: goalId, task_identity: taskIdentity, run_id: runId, date: todayJst }));
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
    '- パイプライン確認: ローカルモックの連結確認のみ（業務タスク完了ではない）',
    '- 実績指標・実投稿: なし（モック値を実績として記録しない）',
    '- 外部投稿/API課金: なし（子プロセスへ資格情報を渡さず、投稿なし）',
    '- 結果書き戻し: AddnessのAIメモリに記録し、同じrun_idを再取得して検証',
    '',
    '実行ログの要約:',
    output.split('\n').filter((line) => /mode:|MOCK|投稿中止|パイプライン完走/.test(line)).join('\n').slice(0, 3000),
  ].join('\n');

  await callTool(appendMemoryName, {
    goal_id: goalId,
    kind: 'progress',
    content: proof,
    evidence: `SNS PR #5 のローカルモック検証。run_id: ${runId}`,
    idempotency_key: runId,
    source_client: 'codex',
  });
  await verifyWriteback({ goalId, runId, listMemoriesName, callTool, unwrap });
  console.log(JSON.stringify({
    status: 'mock_pipeline_verified',
    business_task_completed: false,
    todo_count: 1,
    goal_id: goalId,
    task_identity: taskIdentity,
    run_id: runId,
    goal_title: title,
    external_post: false,
    billed_api: false,
    writeback: 'Addness goal memory',
  }, null, 2));
}

main().catch((error) => {
  console.error(JSON.stringify({ status: 'failed', message: error.message }, null, 2));
  process.exitCode = 1;
});
