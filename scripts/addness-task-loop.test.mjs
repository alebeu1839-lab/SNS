import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createCliTransport } from './addness-cli-transport.mjs';
import { runWorker } from './addness-task-loop.mjs';
import {
  assertMockOnlySummary, buildPipelineEnv, findRunMemory, getRunId, getTaskIdentity, hasRunMemory,
  parsePipelineSummary, rankGoalCandidates, selectGoalFromTree, selectUniqueGoalTodo, validateMcpUrl, verifyWriteback,
} from './addness-task-loop-utils.mjs';

test('MCP URL validation prevents sending the Addness bearer token to other hosts', () => {
  assert.equal(validateMcpUrl('https://vt.api.addness.com/mcp'), 'https://vt.api.addness.com/mcp');
  assert.throws(() => validateMcpUrl('http://vt.api.addness.com/mcp'), /HTTPS/);
  assert.throws(() => validateMcpUrl('https://vt.api.addness.com:8443/mcp'), /default port/);
  assert.throws(() => validateMcpUrl('https://evil.example/mcp'), /addness.com/);
  assert.throws(() => validateMcpUrl('https://user:pass@vt.api.addness.com/mcp'), /credentials/);
});

test('task selection stops when Addness provides no explicit priority for multiple goals', () => {
  assert.deepEqual(selectUniqueGoalTodo([]), { status: 'idle' });
  assert.deepEqual(selectUniqueGoalTodo([{ objectiveId: 'goal-1' }]), { status: 'selected', todo: { objectiveId: 'goal-1' } });
  assert.deepEqual(selectUniqueGoalTodo([{ objectiveId: 'goal-1' }, { objectiveId: 'goal-2' }]), { status: 'needs_priority_decision', count: 2 });
});

test('pipeline subprocess environment is an OS allowlist with approval locked on', () => {
  const env = buildPipelineEnv({
    PATH: 'path', SystemRoot: 'root', TEMP: 'temp', TMP: 'tmp',
    ADDNESS_MCP_TOKEN: 'secret', ANTHROPIC_API_KEY: 'secret', OPENAI_API_KEY: 'secret',
    SOME_TOKEN: 'secret', NODE_OPTIONS: '--require evil.cjs', DEFAULT_MODEL: 'paid-model',
  });
  assert.deepEqual(env, { PATH: 'path', SystemRoot: 'root', TEMP: 'temp', TMP: 'tmp', HUMAN_APPROVAL_REQUIRED: 'true' });
  assert.equal(Object.keys(env).some((key) => /TOKEN|KEY|NODE_OPTIONS/.test(key)), false);
});

test('run identity distinguishes date, goal, and task and is stable for duplicate runs', () => {
  const taskIdentity = getTaskIdentity({ id: 'todo-1', objectiveId: 'goal-1' });
  const base = { date: '2026-10-24', goalId: 'goal-1', taskIdentity };
  const runId = getRunId(base);
  assert.equal(getRunId(base), runId);
  assert.notEqual(getRunId({ ...base, date: '2026-10-25' }), runId);
  assert.notEqual(getRunId({ ...base, goalId: 'goal-2' }), runId);
  assert.notEqual(getRunId({ ...base, taskIdentity: 'id:todo-2' }), runId);
  assert.equal(getTaskIdentity({ title: 'a', objectiveId: 'g' }), getTaskIdentity({ objectiveId: 'g', title: 'a' }));
});

test('writeback is verified by reading the same run_id back from Addness goal memories', async () => {
  const runId = 'run-abc';
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return { content: [{ type: 'text', text: JSON.stringify([{ content: `stored run_id: ${runId}` }]) }] };
  };
  const unwrap = (result) => JSON.parse(result.content[0].text);
  assert.equal(hasRunMemory([{ content: `stored run_id: ${runId}` }], runId), true);
  assert.equal(await verifyWriteback({ goalId: 'goal-full-id', runId, listMemoriesName: 'addness_list_goal_memories', callTool, unwrap }), true);
  assert.deepEqual(calls, [{ name: 'addness_list_goal_memories', args: { goal_id: 'goal-full-id', limit: 100, status: 'active' } }]);
  await assert.rejects(
    verifyWriteback({ goalId: 'goal-full-id', runId: 'missing-run', listMemoriesName: 'addness_list_goal_memories', callTool, unwrap }),
    /memory writeback verification failed/,
  );
});

test('run lookup follows Addness memory cursors beyond the first page', async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    const page = args.cursor
      ? { memories: [{ content: 'run_id: run-older' }], next_cursor: null }
      : { memories: [], next_cursor: 'page-2' };
    return { content: [{ type: 'text', text: JSON.stringify(page) }] };
  };
  const unwrap = (result) => JSON.parse(result.content[0].text);
  assert.equal(await findRunMemory({ goalId: 'goal-full-id', runId: 'run-older', listMemoriesName: 'addness_list_goal_memories', callTool, unwrap }), true);
  assert.deepEqual(calls.map((call) => call.args.cursor || null), [null, 'page-2']);
});

test('local mock pipeline reports no fake posts or fabricated metrics', () => {
  const script = fileURLToPath(new URL('./run-local.mjs', import.meta.url));
  const result = spawnSync(process.execPath, [script, '--goal', 'self-contained test'], {
    encoding: 'utf8',
    env: buildPipelineEnv(process.env),
    timeout: 30_000,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /MOCK \(no API key\)/);
  assert.match(result.stdout, /mock_not_published/);
  assert.match(result.stdout, /metrics: null/);
  assert.match(result.stdout, /unavailable_no_real_posts/);
  assert.doesNotMatch(result.stdout, /4200|0\.058|conversions.*17|mock-12345/);
  assert.match(result.stdout, /業務タスク完了・実投稿・実績分析を示さない/);
  assert.deepEqual(assertMockOnlySummary(parsePipelineSummary(result.stdout)).published, []);
});

// ---------------------------------------------------------------- empty-ToDo goal selection

const tree = [
  { id: 'root', parentId: null, title: 'AIでアフィリエイト収益', status: 'IN_PROGRESS', isCompleted: false, hasChildren: true },
  { id: 'a', parentId: 'root', title: '案件条件を比較表にまとめる', status: 'NONE', isCompleted: false, hasChildren: false },
  { id: 'b', parentId: 'root', title: '投稿草案を検証する', status: 'IN_PROGRESS', isCompleted: false, hasChildren: false },
  { id: 'c', parentId: 'root', title: 'A8.netに提携申請する', status: 'IN_PROGRESS', isCompleted: false, hasChildren: false },
  { id: 'd', parentId: 'root', title: '済んだ作業', status: 'COMPLETED', isCompleted: true, hasChildren: false },
  { id: 'other', parentId: null, title: '住宅ローンを銀行に相談する', status: 'NONE', isCompleted: false, hasChildren: false },
  { id: 'e', parentId: null, title: '別ツリーの作業', status: 'IN_PROGRESS', isCompleted: false, hasChildren: false },
];

test('tree ranking keeps open leaf goals, drops owner-only work, and puts in-progress first', () => {
  assert.deepEqual(rankGoalCandidates(tree).map((goal) => goal.id), ['b', 'e', 'a']);
  assert.deepEqual(rankGoalCandidates(tree, { scopeGoalId: 'root' }).map((goal) => goal.id), ['b', 'a']);
});

test('tree selection skips goals already processed today', async () => {
  const done = new Set(['run:b']);
  const pick = (goals) => selectGoalFromTree({ goals, runIdFor: (goal) => `run:${goal.id}`, hasRun: async (_id, runId) => done.has(runId) });
  assert.equal((await pick(tree)).goal.id, 'e');
  done.add('run:e'); done.add('run:a');
  assert.deepEqual(await pick(tree), { status: 'all_processed_today', candidateCount: 3 });
  assert.deepEqual(await pick([tree[3], tree[4]]), { status: 'idle', candidateCount: 0 });
});

// ---------------------------------------------------------------- mock metric exclusion

const cleanSummary = { mode: 'MOCK', approvalRequired: true, publishStatus: 'mock_not_published', published: [], metrics: null, insights: [], dataStatus: 'unavailable_no_real_posts' };

test('mock-only check refuses posts, metrics, live mode, or a disabled approval gate', () => {
  assert.equal(assertMockOnlySummary(cleanSummary), cleanSummary);
  for (const bad of [
    { published: [{ postId: 'mock-12345' }] },
    { metrics: { impressions: 4200, conversions: 17 } },
    { insights: ['12時が好反応'] },
    { mode: 'LIVE' },
    { approvalRequired: false },
  ]) {
    assert.throws(() => assertMockOnlySummary({ ...cleanSummary, ...bad }), /Mock-only safety check failed/);
  }
  assert.deepEqual(parsePipelineSummary(`log\nPIPELINE_SUMMARY ${JSON.stringify(cleanSummary)}\n`), cleanSummary);
  assert.throws(() => parsePipelineSummary('no summary'), /PIPELINE_SUMMARY/);
});

// ---------------------------------------------------------------- Addness CLI transport

test('CLI transport calls the addness binary with argument arrays and pages through comments', async () => {
  const calls = [];
  const fullPage = Array.from({ length: 1000 }, () => ({ content: 'other' }));
  const run = (args) => {
    calls.push(args);
    if (args[0] === 'comment' && args[1] === 'list') {
      const offset = Number(args[args.indexOf('--offset') + 1]);
      return { status: 0, stdout: JSON.stringify(offset === 0 ? fullPage : [{ content: 'x\nrun_id: r1\n' }]) };
    }
    if (args[0] === 'goal' && args[1] === 'get') return { status: 0, stdout: JSON.stringify({ goal: { id: 'g1', title: 'T', status: 'IN_PROGRESS', body: 'ignored' } }) };
    if (args[0] === 'comment' && args[1] === 'create') {
      const file = args[args.indexOf('--body-file') + 1];
      return { status: 0, stdout: JSON.stringify({ id: 'c1', content: readFileSync(file, 'utf8'), file }) };
    }
    return { status: 0, stdout: '[]' };
  };
  const cli = createCliTransport({ run });
  assert.deepEqual(await cli.listTodayTodos(), []);
  assert.deepEqual(calls[0], ['today', 'todo', 'list', '--json']);
  assert.deepEqual(await cli.getGoal('g1'), { id: 'g1', title: 'T', status: 'IN_PROGRESS' });
  assert.equal(await cli.hasRun('g1', 'r1'), true);
  assert.equal(await cli.hasRun('g1', 'r2'), false);
  const created = await cli.appendRecord({ goalId: 'g1', runId: 'r1', content: '本文 run_id: r1' });
  assert.equal(created.content, '本文 run_id: r1');
  assert.equal(existsSync(created.file), false, 'temporary body file is removed');
});

test('CLI transport surfaces addness errors instead of treating them as empty results', async () => {
  const cli = createCliTransport({ run: () => ({ status: 1, stdout: '', stderr: 'Error: API error (401)' }) });
  await assert.rejects(cli.listTodayTodos(), /today todo failed \(1\): Error: API error \(401\)/);
});

// ---------------------------------------------------------------- whole run with a fake Addness

function fakeAddness({ todos = [], goals = tree, dropWrites = false } = {}) {
  const records = [];
  return {
    records,
    transport: {
      name: 'cli',
      recordKind: 'Addnessゴールのコメント',
      listTodayTodos: async () => todos,
      listGoals: async () => goals,
      getGoal: async (id) => ({ id, title: goals.find((goal) => goal.id === id)?.title || 'ToDo goal', status: 'IN_PROGRESS' }),
      hasRun: async (id, runId) => records.some((record) => record.goalId === id && record.content.includes(`run_id: ${runId}`)),
      appendRecord: async (record) => { if (!dropWrites) records.push(record); },
    },
  };
}
const cleanPipeline = () => `PIPELINE_SUMMARY ${JSON.stringify(cleanSummary)}`;
const now = new Date('2026-10-24T03:00:00Z');

test('empty ToDo: worker picks a goal from the tree, writes once, verifies, then moves on', async () => {
  const addness = fakeAddness();
  const first = await runWorker({ transport: addness.transport, runPipeline: cleanPipeline, now });
  assert.equal(first.status, 'mock_pipeline_verified');
  assert.equal(first.goal_id, 'b');
  assert.equal(first.readback, 'verified');
  assert.equal(addness.records.length, 1);
  assert.match(addness.records[0].content, /ToDo 0件のためゴールツリーから選定/);
  assert.match(addness.records[0].content, /業務タスク完了: いいえ/);
  assert.doesNotMatch(addness.records[0].content, /4200|0\.058|17件/);
  const second = await runWorker({ transport: addness.transport, runPipeline: cleanPipeline, now });
  assert.equal(second.goal_id, 'e', 'the same goal is not processed twice on one day');
});

test('a pipeline that reports metrics or posts never reaches Addness', async () => {
  const addness = fakeAddness();
  const fabricated = () => `PIPELINE_SUMMARY ${JSON.stringify({ ...cleanSummary, metrics: { impressions: 4200, conversions: 17 } })}`;
  await assert.rejects(runWorker({ transport: addness.transport, runPipeline: fabricated, now }), /metrics were reported/);
  assert.equal(addness.records.length, 0);
});

test('dry run, ambiguous ToDos, and a lost write are all handled without a false success', async () => {
  const dry = fakeAddness();
  const preview = await runWorker({ transport: dry.transport, runPipeline: cleanPipeline, now, dryRun: true });
  assert.equal(preview.status, 'dry_run');
  assert.equal(dry.records.length, 0);

  const ambiguous = fakeAddness({ todos: [{ objectiveId: 'a' }, { objectiveId: 'b' }] });
  assert.equal((await runWorker({ transport: ambiguous.transport, runPipeline: cleanPipeline, now })).status, 'mock_pipeline_verified');
  assert.equal(ambiguous.records.length, 1);
  assert.equal(ambiguous.records[0].goalId, 'b', 'in-progress safe goal is preferred');

  const todo = fakeAddness({ todos: [{ id: 't1', objectiveId: 'a' }] });
  assert.equal((await runWorker({ transport: todo.transport, runPipeline: cleanPipeline, now })).goal_id, 'a', 'a ToDo wins over the tree');

  const lost = fakeAddness({ dropWrites: true });
  await assert.rejects(runWorker({ transport: lost.transport, runPipeline: cleanPipeline, now }), /writeback verification failed/);
});

import { buildReviewDraft } from './addness-review-draft.mjs';

test('review draft is goal-specific and never claims publication or verified affiliate terms', () => {
  const a = buildReviewDraft({ goalId: 'goal-a', title: 'A8.net SNS案件の確認', taskIdentity: 'task:1', date: '2026-10-25' });
  const b = buildReviewDraft({ goalId: 'goal-b', title: '中古車店のSNS運用', taskIdentity: 'task:2', date: '2026-10-25' });
  assert.notEqual(a.deliverable, b.deliverable);
  assert.match(a.deliverable, /A8.net SNS案件の確認/);
  assert.match(a.deliverable, /要確認/);
  assert.equal(a.published, false);
  assert.equal(a.business_task_completed, false);
  assert.equal(a.approval_required, true);
  assert.throws(() => buildReviewDraft({ goalId: '', title: 'x', taskIdentity: 't', date: 'd' }), /Missing goalId/);
});

test('worker refuses out-of-scope ToDo before any pipeline or writeback', async () => {
  let calls = 0;
  const transport = {
    name: 'test',
    listTodayTodos: async () => [{ id: 'task1', objectiveId: 'outside', title: 'Draft text' }],
    listGoals: async () => [{ id: 'allowed', parentId: null }, { id: 'outside', parentId: null }],
    getGoal: async () => { calls++; return { title: 'Draft text' }; },
    hasRun: async () => false,
    appendRecord: async () => { calls++; },
  };
  const result = await runWorker({ transport, scopeGoalId: 'allowed', runPipeline: () => { calls++; throw new Error('must not run'); } });
  assert.equal(result.status, 'out_of_scope');
  assert.equal(calls, 0);
});

test('worker refuses owner-only ToDo before pipeline or writeback', async () => {
  let calls = 0;
  const transport = {
    name: 'test',
    listTodayTodos: async () => [{ id: 'task2', objectiveId: 'allowed', title: '外部投稿する' }],
    getGoal: async () => { calls++; return { title: 'SNS task' }; },
    hasRun: async () => false,
    appendRecord: async () => { calls++; },
  };
  const result = await runWorker({ transport, scopeGoalId: 'allowed', runPipeline: () => { calls++; } });
  assert.equal(result.status, 'owner_approval_required');
  assert.equal(calls, 0);
});

test('nine ToDos: worker chooses one safe in-scope goal, skips external action, and verifies writeback', async () => {
  const todos = [
    { objectiveId: 'other' }, { objectiveId: 'c' }, { objectiveId: 'd' },
    { objectiveId: 'a' }, { objectiveId: 'b' }, { objectiveId: 'root' },
    { objectiveId: 'e' }, { objectiveId: 'unknown' }, { objectiveId: 'other' },
  ];
  const addness = fakeAddness({ todos });
  const result = await runWorker({ transport: addness.transport, runPipeline: cleanPipeline, now, scopeGoalId: 'root' });
  assert.equal(result.status, 'mock_pipeline_verified');
  assert.equal(result.goal_id, 'b');
  assert.equal(result.readback, 'verified');
  assert.equal(addness.records.length, 1);
  const again = await runWorker({ transport: addness.transport, runPipeline: cleanPipeline, now, scopeGoalId: 'root' });
  assert.equal(again.goal_id, 'a', 'duplicate task is skipped');
  assert.equal(addness.records.length, 2);
});
