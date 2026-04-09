# Importing Conversations Into Claude Code

Use `import-conversation.py` to convert a claude.ai exported conversation JSON into a Claude Code session JSONL that can be resumed with `claude --resume <session-id>`.

`import-conversation.py` writes into the Claude Code project directory derived from `--cwd`. Claude Code escapes that path by replacing `/` with `-`, so `--cwd /home/alex/repo` writes to `~/.claude/projects/-home-alex-repo/`.

## Commands

```bash
SCRIPTS=~/.claude/skills/claude-context-management/scripts

# Basic import from claude.ai export
python3 $SCRIPTS/import-conversation.py conversation.json \
  --cwd /path/to/project \
  --max-tokens 70000 \
  --use-summary

# Import and continue from an existing Claude Code session
python3 $SCRIPTS/import-conversation.py conversation.json --max-tokens 70000 --use-summary \
  --cwd /path/to/project \
  --append-session ~/.claude/projects/<project-dir>/<session-id>.jsonl
```

## Session JSONL Format Findings

- Records are JSONL, linked via `parentUuid`. The first record has `parentUuid: null`.
- User messages must use plain-string `content`, not array content.
- Assistant messages use array `content` blocks.
- Records must alternate `user -> assistant -> user -> ...`.
- `file-history-snapshot` records do not have a `message` key and must be handled gracefully.

## Tool Calling Findings

- Claude Code will not enable tool calling on resume unless the session history contains at least one real `tool_use` plus `tool_result` pair.
- Synthetic or fabricated tool records do not work.
- The reliable path is `--append-session` with raw Claude Code records copied verbatim from a session that already had successful tool calls.

## Content To Strip During Import

- `[Internal reasoning]` blocks
- Tool annotations like `[Read ...]`, `[Navigated ...]`, or `[Shell: ...]`
- Base64 or encoded data blobs
- Claude Code internal protocol messages such as `<local-command-caveat>` and related markers

## Failure Modes

- Consecutive same-role messages break alternation and can disable tool calling.
- Claude Code internal protocol messages should be filtered before import.

## Budget Guidance

- Claude Code overhead is roughly 15k tokens for system prompt, tools, `CLAUDE.md`, and memory.
- Autocompact reserves roughly 33k tokens when enabled.
- Leave headroom for the model's response.
- `--max-tokens 140000` is a safe upper bound when autocompact is disabled.
