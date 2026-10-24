import { createHash } from 'node:crypto';

// Keep the pipeline child process isolated from credentials and runtime overrides.
// PATH and the listed platform variables are only those needed to start Node reliably.
const CHILD_ENV_KEYS = [
  'PATH', 'SystemRoot', 'WINDIR', 'ComSpec', 'PATHEXT', 'TEMP', 'TMP',
];

export function validateMcpUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error('ADDNESS_MCP_URL must be a valid HTTPS Addness URL.'); }
  const host = url.hostname.toLowerCase();
  if (url.protocol !== 'https:' || !(host === 'addness.com' || host.endsWith('.addness.com')) || url.port || url.username || url.password || url.search || url.hash) {
    throw new Error('ADDNESS_MCP_URL must use HTTPS on the default port of an addness.com host without embedded credentials or query parameters.');
  }
  return url.toString();
}

export function buildPipelineEnv(source = process.env) {
  const env = {};
  for (const key of CHILD_ENV_KEYS) {
    if (source[key] !== undefined) env[key] = source[key];
  }
  env.HUMAN_APPROVAL_REQUIRED = 'true';
  return env;
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableValue(value[key])]));
  }
  return value;
}

export function getTaskIdentity(todo) {
  for (const key of ['id', 'task_id', 'taskId', 'todo_id', 'todoId', 'objective_task_id']) {
    const value = todo?.[key];
    if (value !== undefined && value !== null && String(value).trim()) return `${key}:${String(value).trim()}`;
  }
  return `todo:${createHash('sha256').update(JSON.stringify(stableValue(todo))).digest('hex').slice(0, 24)}`;
}

export function getRunId({ date, goalId, taskIdentity }) {
  return createHash('sha256')
    .update(`${date}\0${goalId}\0${taskIdentity}`)
    .digest('hex');
}

export function selectUniqueGoalTodo(queue) {
  const candidates = queue.filter((row) => row?.objectiveId || row?.objective_id);
  if (candidates.length === 0) return { status: 'idle' };
  if (candidates.length > 1) return { status: 'needs_priority_decision', count: candidates.length };
  return { status: 'selected', todo: candidates[0] };
}

export function hasRunMemory(memories, runId) {
  return JSON.stringify(memories).includes(`run_id: ${runId}`);
}

export async function findRunMemory({ goalId, runId, listMemoriesName, callTool, unwrap }) {
  let cursor;
  do {
    const args = { goal_id: goalId, limit: 100, status: 'active' };
    if (cursor) args.cursor = cursor;
    const page = unwrap(await callTool(listMemoriesName, args));
    if (hasRunMemory(page, runId)) return true;
    cursor = page?.next_cursor || page?.nextCursor || null;
  } while (cursor);
  return false;
}

export async function verifyWriteback({ goalId, runId, listMemoriesName, callTool, unwrap }) {
  if (!await findRunMemory({ goalId, runId, listMemoriesName, callTool, unwrap })) {
    throw new Error(`Addness memory writeback verification failed: run_id ${runId} was not found after readback.`);
  }
  return true;
}
