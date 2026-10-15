# Addness → SNS AI employee loop

This worker handles one Addness goal-backed ToDo per invocation:

1. Connects to the configured Addness MCP endpoint.
2. Reads the current ToDo list and resolves one goal with `get_goal`.
3. Runs the existing SNS pipeline in local mock mode.
4. Writes an execution summary as a comment on the same Addness goal.

## Safety defaults

- The worker strips `ANTHROPIC_API_KEY` and `DEFAULT_MODEL` from the pipeline subprocess, so a run cannot call a paid model.
- It requires the pipeline to report mock mode and the publish gate to stop before it writes a success record.
- It never calls SNS publishing APIs. A later production publishing workflow still needs the user's approval before each external post.
- It does not mark the Addness goal complete; the comment is an auditable result and the owner decides whether the task is done.
- It processes only one goal-backed item per invocation and checks for a same-day result comment first, preventing duplicate execution on a recurring runner. Free-text ToDos and empty queues are left untouched.

## Configure

Supply MCP credentials through the runtime secret store, never a committed `.env` file:

```sh
export ADDNESS_MCP_URL=https://vt.api.addness.com/mcp
export ADDNESS_MCP_TOKEN='<OAuth token from the approved MCP host>'
node scripts/addness-task-loop.mjs
```

If the host injects MCP OAuth automatically, omit `ADDNESS_MCP_TOKEN`. The MCP endpoint must expose `list_today_todos`, `get_goal`, and `add_comment` tools. Run from the repository root; the script resolves its own location and does not depend on the caller's working directory.

For repeated execution, schedule one invocation through the already-approved local runner (for example, n8n). Do not add credentials to GitHub Actions or enable a paid model/publishing credential without owner approval. The worker is intentionally one-item-per-run so each execution has a clear Addness audit comment.
