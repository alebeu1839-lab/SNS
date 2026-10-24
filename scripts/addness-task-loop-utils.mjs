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

// Goals whose titles point at work only the owner can do (money, contracts, external accounts,
// publishing, personal life decisions). The tree selector never picks these automatically.
export const OWNER_ONLY_PATTERN = /本人|銀行|融資|ローン|契約|申請|提携|購入|課金|支払|送金|投資|株式|転職|給与|相談|提案し|ログイン|インストール|投稿する|公開する|送信|DM/;

const OPEN_STATUSES = new Set(['IN_PROGRESS', 'NOT_STARTED', 'NONE']);
const STATUS_RANK = { IN_PROGRESS: 0, NOT_STARTED: 1, NONE: 1 };

function subtreeIds(goals, rootId) {
  const ids = new Set([rootId]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const goal of goals) {
      if (goal.parentId && ids.has(goal.parentId) && !ids.has(goal.id)) { ids.add(goal.id); grew = true; }
    }
  }
  return ids;
}

// Rank open leaf goals for an empty ToDo list: in-progress work first, then the tree's own order.
// Leaves only, because a parent goal is finished through its children.
export function rankGoalCandidates(goals, { scopeGoalId } = {}) {
  const inScope = scopeGoalId ? subtreeIds(goals, scopeGoalId) : null;
  return goals
    .map((goal, index) => ({ goal, index }))
    .filter(({ goal }) => goal?.id && !goal.isCompleted && !goal.hasChildren && OPEN_STATUSES.has(goal.status))
    .filter(({ goal }) => !inScope || inScope.has(goal.id))
    .filter(({ goal }) => !OWNER_ONLY_PATTERN.test(goal.title || ''))
    .sort((a, b) => (STATUS_RANK[a.goal.status] - STATUS_RANK[b.goal.status]) || (a.index - b.index))
    .map(({ goal }) => goal);
}

// Pick the first ranked goal that has not already been processed today.
export async function selectGoalFromTree({ goals, scopeGoalId, runIdFor, hasRun, maxChecks = 20 }) {
  const ranked = rankGoalCandidates(goals, { scopeGoalId });
  if (ranked.length === 0) return { status: 'idle', candidateCount: 0 };
  for (const goal of ranked.slice(0, maxChecks)) {
    if (!await hasRun(goal.id, runIdFor(goal))) return { status: 'selected', goal, candidateCount: ranked.length };
  }
  return { status: 'all_processed_today', candidateCount: ranked.length };
}

// The pipeline must prove it stayed in mock mode, posted nothing, and reported no metrics.
// Anything else (a post, a number) is refused so mock values can never be recorded as results.
export function assertMockOnlySummary(summary) {
  const problems = [];
  if (summary?.mode !== 'MOCK') problems.push(`mode=${summary?.mode}`);
  if (summary?.approvalRequired !== true) problems.push('approval gate is off');
  if (!Array.isArray(summary?.published) || summary.published.length !== 0) problems.push('published is not empty');
  if (summary?.metrics !== null) problems.push('metrics were reported');
  if (!Array.isArray(summary?.insights) || summary.insights.length !== 0) problems.push('insights were reported');
  if (problems.length) throw new Error(`Mock-only safety check failed: ${problems.join(', ')}`);
  return summary;
}

export function parsePipelineSummary(stdout) {
  const line = stdout.split(/\r?\n/).reverse().find((row) => row.startsWith('PIPELINE_SUMMARY '));
  if (!line) throw new Error('Pipeline did not print PIPELINE_SUMMARY.');
  return JSON.parse(line.slice('PIPELINE_SUMMARY '.length));
}
