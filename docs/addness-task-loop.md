# Addness → SNS AI employee loop

This worker handles one Addness goal-backed ToDo per invocation:

1. Connects to the configured Addness MCP endpoint.
2. Reads the current ToDo list and resolves one goal with `get_goal`.
3. Runs the existing SNS pipeline in local mock mode.
4. Appends an execution summary to AI memory on the same Addness goal and reads it back by `run_id`.

## Safety defaults

- The worker gives the pipeline subprocess a small operating-system environment allowlist, so credentials and runtime overrides cannot reach a paid model.
- It accepts MCP URLs only over HTTPS on the default port of an `addness.com` host and rejects embedded credentials, query parameters, and fragments before sending the bearer token.
- It requires the pipeline to report mock mode and the publish gate to stop before it writes a success record.
- It never calls SNS publishing APIs. A later production publishing workflow still needs the user's approval before each external post.
- It does not mark the Addness goal complete; its AI memory says explicitly that this is a mock-only check, and the owner decides whether the task is done.
- It processes one goal-backed item only when it is the sole candidate. If Addness returns multiple goals without an ordering contract, it stops with `needs_priority_decision`; free-text ToDos and empty queues are left untouched.
- It follows Addness memory cursors for idempotency checks and writeback readback, so older matching runs are not missed after the first page.

## Configure

Supply MCP credentials through the runtime secret store, never a committed `.env` file:

```sh
export ADDNESS_MCP_URL=https://vt.api.addness.com/mcp
export ADDNESS_MCP_TOKEN='<OAuth token from the approved MCP host>'
node scripts/addness-task-loop.mjs
```

If the host injects MCP OAuth automatically, omit `ADDNESS_MCP_TOKEN`. The MCP endpoint must expose `list_today_todos`, `get_goal`, `append_goal_memory`, and `list_goal_memories` tools. Run from the repository root; the script resolves its own location and does not depend on the caller's working directory.

For repeated execution, schedule one invocation through the already-approved local runner (for example, n8n). Do not add credentials to GitHub Actions or enable a paid model/publishing credential without owner approval. The worker is intentionally one-item-per-run so each execution has a clear Addness memory record.
