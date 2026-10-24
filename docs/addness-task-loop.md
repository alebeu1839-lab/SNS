# Addness → SNS AI employee loop

This worker handles one Addness task per invocation:

1. Connects to Addness through the logged-in `addness` CLI (default) or an Addness MCP endpoint.
2. Picks one goal:
   - If today's ToDo list has exactly one goal-backed row, that goal.
   - If it has several, it stops with `needs_priority_decision`.
   - If it is empty, it picks one open leaf goal from the goal tree (see below).
3. Runs the existing SNS pipeline in local mock mode and checks its structured `PIPELINE_SUMMARY`.
4. Records the result on the same goal (a comment over CLI, AI memory over MCP) and reads it back by `run_id`.

## Empty-ToDo selection

Candidates are goals that are open (`IN_PROGRESS`, `NOT_STARTED`, `NONE`), not completed, and have no children.
Goals whose titles name owner-only work (banks, loans, contracts, applications, partnerships, purchases, payments,
logins, installs, DMs, posting, `〔本人〕`, and so on; see `OWNER_ONLY_PATTERN`) are never picked.
In-progress goals come first, then the tree's own order. A goal already processed today (same `run_id`) is skipped,
so repeated runs on one day move to the next candidate and stop with `all_processed_today` when none are left.

Set `ADDNESS_SCOPE_GOAL_ID` to limit selection to one goal's subtree. Without it the whole organisation tree is
considered, which includes goals the SNS pipeline has nothing to do with.

## Safety defaults

- The pipeline subprocess gets a small operating-system environment allowlist, so credentials and runtime overrides cannot reach a paid model.
- MCP URLs are accepted only over HTTPS on the default port of an `addness.com` host, without embedded credentials, query parameters, or fragments.
- The run is recorded only if `PIPELINE_SUMMARY` reports mock mode, the approval gate on, zero posts, `metrics: null`, and no insights. A post or any metric (for example the old mock `conversions: 17`) fails the run before anything is written.
- `run-local.mjs` no longer calls the analyst when nothing was published, so neither the mock nor a live model can invent metrics.
- It never calls SNS publishing APIs. A later production publishing workflow still needs the user's approval before each external post.
- It does not mark the Addness goal complete; the record says it is a mock-only check, and the owner decides whether the task is done.
- A write counts only after the same `run_id` is found again on readback (comment and memory lookups page through all results).

## Run

From the repository root, with the Addness CLI logged in (`addness login`):

```sh
node scripts/addness-task-loop.mjs --dry-run   # select and run the pipeline, write nothing
node scripts/addness-task-loop.mjs             # also record on the goal and verify by readback
```

`ADDNESS_CLI` overrides the CLI path; otherwise the worker uses `%LOCALAPPDATA%\addness\bin\addness.exe` when it exists, then `addness` on `PATH`.

To use MCP instead, supply credentials through the runtime secret store, never a committed `.env` file:

```sh
export ADDNESS_MCP_URL=https://vt.api.addness.com/mcp
export ADDNESS_MCP_TOKEN='<OAuth token from the approved MCP host>'
node scripts/addness-task-loop.mjs
```

The MCP endpoint must expose `list_today_todos`, `get_goal`, `append_goal_memory`, and `list_goal_memories`; empty-ToDo selection also needs `get_goal_tree`, and without it the worker stays idle.

For repeated execution, schedule one invocation through an already-approved local runner (for example, Windows Task Scheduler or n8n). Do not add credentials to GitHub Actions or enable a paid model/publishing credential without owner approval. The worker is intentionally one-item-per-run so each execution has a clear Addness record.
