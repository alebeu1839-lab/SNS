import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { buildPipelineEnv, findRunMemory, getRunId, getTaskIdentity, hasRunMemory, selectUniqueGoalTodo, validateMcpUrl, verifyWriteback } from './addness-task-loop-utils.mjs';

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
});
