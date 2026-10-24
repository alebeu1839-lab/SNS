#!/usr/bin/env node
/**
 * Run one Addness task through the SNS pipeline in a no-post, no-billing mock run.
 *
 * Transport (how the worker reaches Addness):
 *   - default: the logged-in `addness` CLI (ADDNESS_CLI overrides the binary path)
 *   - MCP: set ADDNESS_MCP_URL (for example https://vt.api.addness.com/mcp), plus
 *     ADDNESS_MCP_TOKEN when the host does not inject OAuth.
 * Optional: ADDNESS_SCOPE_GOAL_ID limits empty-ToDo selection to one goal's subtree.
 * Flags: --dry-run selects and runs the pipeline but writes nothing to Addness.
 * The pipeline child process gets only an OS environment allowlist, never credentials.
 */
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createCliTransport } from './addness-cli-transport.mjs';
import {
  assertMockOnlySummary, buildPipelineEnv, findRunMemory, getRunId, getTaskIdentity,
  parsePipelineSummary, selectGoalFromTree, selectUniqueGoalTodo, validateMcpUrl,
} from './addness-task-loop-utils.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const VERSION = '2025-03-26';

// ---------------------------------------------------------------- MCP transport

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

function unwrap(result) {
  if (result?.structuredContent) return result.structuredContent;
  for (const part of result?.content || []) {
    if (part.type !== 'text' || !part.text) continue;
    try { return JSON.parse(part.text); } catch { return part.text; }
  }
  return result;
}

function extractList(value, keys) {
  if (Array.isArray(value)) return value;
  for (const key of keys) {
    if (Array.isArray(value?.[key])) return value[key];
  }
  return [];
}

// Normalise a nested MCP goal tree into the flat shape the CLI returns.
function flattenGoalTree(nodes, parentId = null, out = []) {
  for (const node of nodes) {
    const children = extractList(node.children, ['children', 'nodes']);
    out.push({
      id: node.id || node.goal_id,
      parentId: node.parentId || node.parent_id || parentId,
      title: node.title || '',
      status: node.status,
      isCompleted: Boolean(node.isCompleted || node.is_completed),
      hasChildren: Boolean(node.hasChildren ?? node.has_children ?? children.length),
    });
    flattenGoalTree(children, node.id || node.goal_id, out);
  }
  return out;
}

async function createMcpTransport({ url, token }) {
  let requestId = 0;
  let sessionId;
  let protocolVersion = VERSION;

  async function rpc(method, params, notification = false) {
    const headers = { accept: 'application/json, text/event-stream', 'content-type': 'application/json' };
    if (token) headers.authorization = `Bearer ${token}`;
    if (sessionId) headers['mcp-session-id'] = sessionId;
    if (protocolVersion) headers['mcp-protocol-version'] = protocolVersion;
    const body = { jsonrpc: '2.0', method, params };
    if (!notification) body.id = ++requestId;
    const response = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
    const freshSession = response.headers.get('mcp-session-id');
    if (freshSession) sessionId = freshSession;
    if (response.status === 202 && notification) return null;
    const raw = await response.text();
    if (!response.ok) throw new Error(`MCP HTTP ${response.status}: ${raw.slice(0, 600)}`);
    const message = parseRpc(raw);
    if (message?.error) throw new Error(`MCP ${method}: ${message.error.message || JSON.stringify(message.error)}`);
    return message?.result;
  }

  async function callTool(name, args) {
    const result = await rpc('tools/call', { name, arguments: args });
    if (result?.isError) {
      const details = result.content?.map((part) => part.text || '').join('\n');
      throw new Error(`Addness tool failed: ${details || name}`);
    }
    return result;
  }

  const initialized = await rpc('initialize', {
    protocolVersion: VERSION,
    capabilities: {},
    clientInfo: { name: 'sns-addness-task-loop', version: '0.2.0' },
  });
  protocolVersion = initialized?.protocolVersion || VERSION;
  await rpc('notifications/initialized', {}, true);
  const tools = (await rpc('tools/list', {}))?.tools || [];
  const findTool = (suffix) => tools.find((tool) => tool.name === suffix || tool.name.endsWith(`_${suffix}`))?.name;
  const requireTool = (suffix) => {
    const name = findTool(suffix);
    if (!name) throw new Error(`Addness MCP tool is unavailable: *${suffix}`);
    return name;
  };
  const listName = requireTool('list_today_todos');
  const getGoalName = requireTool('get_goal');
  const appendMemoryName = requireTool('append_goal_memory');
  const listMemoriesName = requireTool('list_goal_memories');
  const treeName = findTool('get_goal_tree');

  return {
    name: 'mcp',
    recordKind: 'AddnessゴールのAIメモリ',
    async listTodayTodos() {
      return extractList(unwrap(await callTool(listName, {})), ['todos', 'items', 'tasks', 'data']);
    },
    async listGoals() {
      if (!treeName) return null;
      return flattenGoalTree(extractList(unwrap(await callTool(treeName, {})), ['goals', 'nodes', 'tree', 'data']));
    },
    async getGoal(goalId) {
      const result = unwrap(await callTool(getGoalName, { goal_id: goalId }));
      const text = typeof result === 'string' ? result : JSON.stringify(result);
      const title = result?.title || text.match(/^#\s+\[[^\]]+\]\s+(.+)$/m)?.[1]?.trim() || 'Addness ToDo';
      return { id: goalId, title, status: result?.status || null };
    },
    async hasRun(goalId, runId) {
      return findRunMemory({ goalId, runId, listMemoriesName, callTool, unwrap });
    },
    async appendRecord({ goalId, runId, content }) {
      return callTool(appendMemoryName, {
        goal_id: goalId,
        kind: 'progress',
        content,
        evidence: `SNS addness-task-loop mock run. run_id: ${runId}`,
        idempotency_key: runId,
        source_client: 'sns-addness-task-loop',
      });
    },
  };
}

// ---------------------------------------------------------------- pipeline

function runMockPipeline(title) {
  const result = spawnSync(process.execPath, ['scripts/run-local.mjs', '--goal', title], {
    cwd: ROOT,
    env: buildPipelineEnv(process.env),
    encoding: 'utf8',
    timeout: 120_000,
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error((result.stderr || result.stdout || 'SNS mock run failed').slice(-2000));
  return result.stdout || '';
}

// ---------------------------------------------------------------- one run

export async function runWorker({ transport, runPipeline = runMockPipeline, now = new Date(), scopeGoalId, dryRun = false }) {
  const date = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(now);
  const base = { date, transport: transport.name };

  let goalId;
  let taskIdentity;
  let selectionNote;
  const todoSelection = selectUniqueGoalTodo(await transport.listTodayTodos());
  if (todoSelection.status === 'needs_priority_decision') {
    return { ...base, status: 'needs_priority_decision', goal_backed_todo_count: todoSelection.count, reason: 'Multiple goal-backed ToDos and no ordering contract; nothing was selected.' };
  }
  if (todoSelection.status === 'selected') {
    goalId = todoSelection.todo.objectiveId || todoSelection.todo.objective_id;
    taskIdentity = getTaskIdentity(todoSelection.todo);
    selectionNote = '今日のToDo（目標つき1件）';
    if (await transport.hasRun(goalId, getRunId({ date, goalId, taskIdentity }))) {
      return { ...base, status: 'already_processed', goal_id: goalId, source: 'today_todo' };
    }
  } else {
    // ToDo is empty: pick one open leaf goal from the tree (see rankGoalCandidates for the rules).
    const goals = await transport.listGoals();
    if (!goals) return { ...base, status: 'idle', reason: 'ToDo is empty and this transport cannot list the goal tree.' };
    const identityFor = (goal) => `goal:${goal.id}`;
    const treeSelection = await selectGoalFromTree({
      goals,
      scopeGoalId,
      runIdFor: (goal) => getRunId({ date, goalId: goal.id, taskIdentity: identityFor(goal) }),
      hasRun: (id, runId) => transport.hasRun(id, runId),
    });
    if (treeSelection.status !== 'selected') {
      return { ...base, status: treeSelection.status, candidate_count: treeSelection.candidateCount, reason: 'ToDo is empty and no eligible goal is left for today.' };
    }
    goalId = treeSelection.goal.id;
    taskIdentity = identityFor(treeSelection.goal);
    selectionNote = `ToDo 0件のためゴールツリーから選定（候補${treeSelection.candidateCount}件中、進行中を優先しツリー順で先頭。本人限定の作業は除外${scopeGoalId ? `、範囲 ${scopeGoalId} 配下` : ''}）`;
  }

  const runId = getRunId({ date, goalId, taskIdentity });
  const goal = await transport.getGoal(goalId);
  const summary = assertMockOnlySummary(parsePipelineSummary(runPipeline(goal.title)));

  const content = [
    `## AI社員ループのモック検証（${date}）`,
    '',
    `run_id: ${runId}`,
    `goal_id: ${goalId}`,
    `task_identity: ${taskIdentity}`,
    '',
    `- 選定: ${selectionNote}`,
    `- 対象ゴール: ${goal.title}`,
    `- 接続: ${transport.name === 'cli' ? 'Addness CLI' : 'Addness MCP'}`,
    `- パイプライン: ${summary.mode}モード、承認ゲート有効、投稿 ${summary.published.length}件（${summary.publishStatus}）`,
    `- 実績指標: なし（${summary.dataStatus}）。モックの値は記録しない`,
    '- 業務タスク完了: いいえ（連結確認のみ。完了判断は本人）',
    '- 外部投稿・API課金: なし',
    `- 書き戻し: この${transport.recordKind}。同じrun_idを再取得して検証`,
  ].join('\n');

  const result = { ...base, goal_id: goalId, goal_title: goal.title, task_identity: taskIdentity, run_id: runId, business_task_completed: false, external_post: false, billed_api: false };
  if (dryRun) return { ...result, status: 'dry_run', record_preview: content };

  await transport.appendRecord({ goalId, runId, content });
  if (!await transport.hasRun(goalId, runId)) {
    throw new Error(`Addness writeback verification failed: run_id ${runId} was not found after readback.`);
  }
  return { ...result, status: 'mock_pipeline_verified', writeback: transport.recordKind, readback: 'verified' };
}

async function main() {
  const transport = process.env.ADDNESS_MCP_URL
    ? await createMcpTransport({ url: validateMcpUrl(process.env.ADDNESS_MCP_URL), token: process.env.ADDNESS_MCP_TOKEN })
    : createCliTransport();
  const result = await runWorker({
    transport,
    scopeGoalId: process.env.ADDNESS_SCOPE_GOAL_ID || undefined,
    dryRun: process.argv.includes('--dry-run'),
  });
  console.log(JSON.stringify(result, null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(JSON.stringify({ status: 'failed', message: error.message }, null, 2));
    process.exitCode = 1;
  });
}
