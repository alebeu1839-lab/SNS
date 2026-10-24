import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Talks to Addness through the official, already-logged-in `addness` CLI, so the worker needs no
// OAuth token of its own. Results are recorded as comments on the goal (the CLI has no memory API).

const COMMENT_PAGE = 1000;

export function resolveAddnessCli(env = process.env) {
  if (env.ADDNESS_CLI) return env.ADDNESS_CLI;
  const windowsDefault = env.LOCALAPPDATA && join(env.LOCALAPPDATA, 'addness', 'bin', 'addness.exe');
  if (windowsDefault && existsSync(windowsDefault)) return windowsDefault;
  return 'addness';
}

function defaultRun(bin) {
  return (args) => spawnSync(bin, args, { encoding: 'utf8', timeout: 60_000, maxBuffer: 16 * 1024 * 1024, windowsHide: true });
}

export function createCliTransport({ bin = resolveAddnessCli(), run = defaultRun(bin), treeDepth = 6 } = {}) {
  function cli(args) {
    const result = run(args);
    if (result.error) throw new Error(`addness ${args.slice(0, 2).join(' ')}: ${result.error.message}`);
    if (result.status !== 0) {
      throw new Error(`addness ${args.slice(0, 2).join(' ')} failed (${result.status}): ${(result.stderr || result.stdout || '').trim().slice(0, 300)}`);
    }
    return result.stdout;
  }
  const cliJson = (args) => JSON.parse(cli(args));

  async function listComments(goalId) {
    const all = [];
    for (let offset = 0; ; offset += COMMENT_PAGE) {
      const page = cliJson(['comment', 'list', '--goal', goalId, '--json', '--limit', String(COMMENT_PAGE), '--offset', String(offset)]);
      all.push(...page);
      if (page.length < COMMENT_PAGE) return all;
    }
  }

  return {
    name: 'cli',
    recordKind: 'Addnessゴールのコメント',
    async listTodayTodos() {
      return cliJson(['today', 'todo', 'list', '--json']);
    },
    async listGoals() {
      return cliJson(['goal', 'list', '--depth', String(treeDepth), '--json']);
    },
    async getGoal(goalId) {
      const goal = cliJson(['goal', 'get', goalId, '--json']).goal;
      return { id: goal.id, title: goal.title, status: goal.status };
    },
    async hasRun(goalId, runId) {
      return (await listComments(goalId)).some((comment) => (comment.content || '').includes(`run_id: ${runId}`));
    },
    async appendRecord({ goalId, content }) {
      const dir = mkdtempSync(join(tmpdir(), 'addness-loop-'));
      try {
        const file = join(dir, 'record.md');
        writeFileSync(file, content, 'utf8');
        return cliJson(['comment', 'create', '--goal', goalId, '--body-file', file, '--json']);
      } finally {
        rmSync(dir, { recursive: true, force: true });
      }
    },
  };
}
