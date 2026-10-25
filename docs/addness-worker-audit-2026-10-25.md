# Addness AI worker audit — 2026-10-25

## Scope and evidence
- Reviewed `scripts/addness-task-loop.mjs`, `scripts/addness-task-loop-utils.mjs` on `codex/addness-task-loop`.
- Reviewed default-branch `.github/workflows/mobile-ai-employee.yml`.
- Live Addness `list_today_todos` returned **9 goal-backed ToDos** (no priority or title fields in the list).

## Verified findings
1. **BLOCKER — Multi-ToDo dead end.** `selectUniqueGoalTodo` returns `needs_priority_decision` whenever there is more than one goal-backed ToDo. `runWorker` immediately exits before creating any review draft or writing a result. This applies to the current nine-item queue. Do not label this a successful autonomous task run.
2. **BLOCKER — Cloud Addness credentials.** The mobile workflow's Addness mode requires an `ADDNESS_MCP_TOKEN` repository secret. Cloud authentication and the full Addness read/write/readback flow have not been demonstrated. Never put credentials in the public repository, workflow files, logs, or Addness memories.
3. **BLOCKER — Not scheduled.** The mobile workflow only has `push` and `workflow_dispatch` triggers. No cron trigger is configured. Previous successful smartphone dispatches generated an unpublished generic draft only.
4. **Correct safety guard.** The worker requires mock-only pipeline output, approval gate enabled, no published posts, and no reported metrics; its child process receives an allowlisted environment rather than the Addness token.
5. **Risk — Hard-coded detached commit.** The default workflow fetches and checks out a fixed worker commit (`cdb67194...`) rather than an explicitly reviewed moving ref. Changes on the feature branch do not reach the cloud worker automatically.
6. **Risk — Goal scope vs queue.** With a single ToDo, scope ancestry verification depends on the goal tree. When ancestry cannot be verified, it fails closed; this is preferable to silently operating outside scope.
7. **Mock output is not business completion.** Review draft creation must not mark any actual affiliate, SNS, or client task complete.

## Proposed implementation (not yet applied)
- Add a deterministic, explainable ToDo priority policy with explicit safe-eligibility checks, and unit tests covering nine ToDos, ambiguous priority, out-of-scope goals, owner-only tasks, and duplicate runs. On ambiguous or missing evidence, stop safely and write a diagnostic only if a permitted goal is known.
- Keep mock-only/no-post/no-billing invariants until an explicit approval mechanism is implemented.
- Move the cloud worker to a reviewed, pinned commit/ref, and make draft and Addness jobs separate so a failed Addness attempt cannot look like a completed business task.
- Add a one-off manual Addness integration test after a secret is configured; only then add a scheduled trigger and test actual unattended read→write→readback.
- Report three separate statuses: pipeline success, Addness writeback success, business-task completion.

## Acceptance criteria
A test with the current nine-ToDo queue chooses exactly one safe in-scope task (or returns an actionable, verified no-selection reason); no external posts, affiliate applications, or billing; verified Addness readback; no secret leakage; repeat execution is idempotent. A scheduled run is **not** considered proven until its own run log and Addness record match.
