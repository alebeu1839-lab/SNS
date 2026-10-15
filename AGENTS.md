# AI SNS Company — Codex operating instructions

## Priorities
- Addness is the source of truth for user-approved goals and task states; GitHub is the source of truth for code.
- When an Addness-related task starts, inspect the relevant Addness goal **using the Codex environment's own Addness MCP connection**. Do not mistake ChatGPT's connection for Codex authentication.
- Main pilot goal: `8249fcb7-5d7f-4e89-a80f-f6fb283d8574`.
- Follow `docs/addness-codex-loop-mvp.md` for the first read → work → append → readback verification.
- Use Claude Code CLI only if a second model adds value. Do not make paid API calls or silently use `ANTHROPIC_API_KEY`.
- **Never publish to X/Threads, apply to A8.net programs, purchase, or make contracts without explicit user approval.** Keep unverified affiliate terms and drafts private.
- Do not use the legacy `scripts/run-local.mjs` for an unattended live test: it may use a billed Anthropic API key and its example approval branch continues as though approved.
- For every real run, include unique run ID, actual outcome, evidence, errors, and next step in Addness. Read back to verify. Do not claim success based on logging alone.
- If Addness MCP authorization is missing, stop, report BLOCKED_CONNECTION, and let the user authorize in the provider UI; never ask for raw tokens/passwords in chat.

## Test status
GitHub Actions mock handoff tests passed. A prior Codex → Claude Code live single-call smoke test was reported successful in Addness. **Codex Addness read/write/readback and unattended execution are not yet validated.**
