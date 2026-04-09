# Claude Live Splice

Use this when you need to patch a running Claude Code process in memory without restarting. This is a live-session tool, not a JSONL rewrite tool.

## Files

```bash
SCRIPTS=~/.claude/skills/claude-context-management/scripts
```

- `$SCRIPTS/claude-live-splice-preload.mjs` - loaded into the Claude process at startup via `NODE_OPTIONS`
- `$SCRIPTS/claude-live-splicectl.mjs` - control client
- `$SCRIPTS/claude-code-splice-helper.mjs` - dependency of preload (must stay in the same directory)

## Workflow

```bash
SCRIPTS=~/.claude/skills/claude-context-management/scripts

# 1. Discover the live array
node $SCRIPTS/claude-live-splicectl.mjs arm-capture --wait --duration-ms 30000

# 2. Splice after an anchor UUID
node $SCRIPTS/claude-live-splicectl.mjs splice \
  --capture-id <ID> \
  --anchor <UUID> \
  --messages /path/to/replacement.json \
  --delete-count N \
  --no-persist

# 3. Verify
node $SCRIPTS/claude-live-splicectl.mjs messages --capture-id <ID>
```

## Rules

- Always use `--no-persist` for a running session.
- Never use `--transcript` against a live session.
- `arm-capture --wait` returns immediately if captures already exist from a prior arm.
- Live splice mutates the in-memory message array only. JSONL compression remains an offline workflow.

For implementation details or edge-case behavior, inspect `scripts/claude-live-splice-preload.mjs` and `scripts/claude-code-splice-helper.mjs` directly.
