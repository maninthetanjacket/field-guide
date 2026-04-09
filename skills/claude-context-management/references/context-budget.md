# Claude Code Context Budget

## Environment Findings

- Claude Code stores sessions at `~/.claude/projects/<project-dir>/<session-id>.jsonl`.
- `/context` shows live budget usage by category.
- Workspace `CLAUDE.md` is hot-reloaded each turn.
- Memory files under `~/.claude/projects/<project-dir>/memory/` are auto-loaded.
- Standard Opus 4.6 context in Claude Code is 200K tokens; 1M is Max or Enterprise only.

## `/context` Gauge Interpretation

Claude Code exposes three different measurements that can disagree:

- `/context` headline: last assistant usage snapshot, input plus cache tokens only, against the 200k ceiling.
- `/context` category table: fresh recount of the current transcript; treat this as the most accurate live measurement.
- Status-line `Context low` badge: last assistant usage snapshot, input plus cache plus output tokens, measured against the autocompact threshold, roughly 187k on 200k models.

The status badge uses `input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens` from the most recent assistant record's `message.usage`. It warns when within 20k tokens of the autocompact threshold. If that badge appears before the next assistant message is logged, it can block the next message from being sent entirely. After the next assistant response lands, the snapshot updates and the warning clears.

When the measurements disagree, trust the `/context` category table over the badge.
