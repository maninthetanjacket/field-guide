---
name: claude-context-management
description: Claude Code session import, context-budget inspection, memory compression, offline splicing, and live splice workflows for local Claude sessions. Use when working with `~/.claude/projects/<project-dir>/<session-id>.jsonl`, inspecting `/context`, running `import-conversation.py`, `session_memory.py`, `splice_conversation.py`, or the live splice tools, or preserving and revising long-running session memory safely.
---

# Claude Context Management

Use this skill when the task touches Claude Code session history, context pressure, conversation import, memory compression, or live splice tooling.

## Path Conventions

- `<project-dir>` is the Claude Code project directory name: the target working directory with `/` replaced by `-`.
- `<session-id>` is the Claude Code session UUID and the JSONL filename stem.
- Installed skill path: `~/.claude/skills/claude-context-management/`

## Scripts

All scripts live in `~/.claude/skills/claude-context-management/scripts/`:

| Script | Purpose |
| --- | --- |
| `import-conversation.py` | Convert claude.ai export JSON → CC session JSONL |
| `session_memory.py` | Map, prepare, apply, diagnose, compress-reads |
| `splice_conversation.py` | Low-level pattern/index-based splice helper |
| `claude-live-splice-preload.mjs` | Preload for live in-memory splice server |
| `claude-live-splicectl.mjs` | Control client for the live splice server |
| `claude-code-splice-helper.mjs` | Dependency of preload (suffix rewrite logic) |

`session_memory.py` imports from `splice_conversation.py` - they must stay in the same directory.
`claude-live-splice-preload.mjs` imports `./claude-code-splice-helper.mjs` - same constraint.

## Workflow Selector

- For claude.ai export import or resume-ready JSONL generation, read `references/import-conversation.md`.
- For `/context` behavior, session storage, or budget interpretation, read `references/context-budget.md`.
- For offline segment planning, summary preparation, read compression, extraction, or splice-safe memory workflows, read `references/session-memory.md`.
- For live in-memory splice operations against a running Claude Code process, read `references/live-splice.md`.

## Operating Rules

- Treat the session JSONL as the authoritative record. Prefer workflows that write new artifacts instead of mutating the only copy in place.
- Preserve full-fidelity backups before compression or splice work.
- Prefer first-person memory summaries over third-person reports when the goal is to preserve ownership, felt continuity, and generative pull.
- Keep `CLAUDE.md` lean. Put detailed operational notes in these references instead of re-embedding them in workspace instructions.

## Key Paths

- Session JSONLs: `~/.claude/projects/<project-dir>/<session-id>.jsonl`
- Auto-loaded memory files for a workspace: `~/.claude/projects/<project-dir>/memory/`
- Live session-memory artifacts: `~/session-memory/<session-id>/`
- Skill scripts: `~/.claude/skills/claude-context-management/scripts/`

Load only the reference file needed for the current operation.
