---
name: arc-chat-adapter
description: Send or continue conversations inside the local Arc Chat instance from Claude Code. Use when the user wants CC-driven conversations to appear in Arc Chat, wants to create a new Arc Chat thread, append a manual turn, run the sensory-stone flow through Arc Chat instead of calling the model API directly, or have multiple CC instances use Arc Chat as a shared transport.
---

# Arc Chat Adapter

Use the local adapter script in the Arc Chat backend repo:

- Adapter script: `animachat/deprecated-claude-app/backend/scripts/arc-chat-adapter.mjs`
- Backend repo: `animachat/deprecated-claude-app/backend`
- Backend URL: `http://localhost:3010`
- Frontend URL: `http://localhost:5173`

Default local auth for development:

- Email: `test@example.com`
- Password: `password123`

The adapter already works with the local Arc Chat instance and resolves friendly model aliases like `gpt-5.4` to the correct Arc Chat model id.

## When To Use

Use this skill when the user wants any of the following:

- a Claude Code action to create or continue an Arc Chat conversation
- a response to land in Arc Chat instead of staying only in the CLI
- a manual assistant turn inserted into an Arc Chat thread
- the sensory-stone protocol routed through Arc Chat
- multiple CC instances to post turns into the same Arc Chat conversation and wait for each other

## Quick Start

Run commands from the backend directory:

```bash
cd animachat/deprecated-claude-app/backend
```

## Send A Normal Chat Turn

Create a new Arc Chat conversation:

```bash
node scripts/arc-chat-adapter.mjs chat \
  --email test@example.com \
  --password password123 \
  --model gpt-5.4 \
  --title "CC Conversation" \
  --message "Reply with exactly: adapter ok"
```

Continue an existing conversation:

```bash
node scripts/arc-chat-adapter.mjs chat \
  --email test@example.com \
  --password password123 \
  --conversation-id <conversation-id> \
  --message "Continue from here."
```

Use `--json` when you want machine-readable output including `conversationId`, `conversationUrl`, and the assistant message ids.

## Append A Manual Assistant Turn

Use this when you need to insert an assistant-authored message without triggering generation:

```bash
node scripts/arc-chat-adapter.mjs append-assistant \
  --email test@example.com \
  --password password123 \
  --conversation-id <conversation-id> \
  --content-file /path/to/message.txt
```

You can also use `--content "text here"` for short inserts.

## Transport Mode For CC-To-CC Chats

When multiple CC instances are talking to each other through Arc Chat, prefer the transport commands instead of `chat`. These commands do not ask Arc Chat to generate a response.

Post a manual turn as a participant:

```bash
node scripts/arc-chat-adapter.mjs post-message \
  --email test@example.com \
  --password password123 \
  --conversation-id <conversation-id> \
  --participant CC-1 \
  --role assistant \
  --content "Hello from CC-1."
```

Wait for the next reply from another participant:

```bash
node scripts/arc-chat-adapter.mjs wait-message \
  --email test@example.com \
  --password password123 \
  --conversation-id <conversation-id> \
  --from CC-2 \
  --after-message-id <message-id-from-post> \
  --timeout-ms 300000
```

If a wait times out, fetch the current latest matching message:

```bash
node scripts/arc-chat-adapter.mjs last-message \
  --email test@example.com \
  --password password123 \
  --conversation-id <conversation-id> \
  --from CC-2 \
  --after-message-id <message-id-from-post>
```

Useful filters:

- `--from <participant>` waits for a specific participant
- `--exclude-participant <participant>` waits for anyone except that participant
- `--role assistant|user` narrows by role
- `--after-message-id <id>` restricts `wait-message` and `last-message` to newer turns
- `--json` returns message ids and participant ids for scripting

## Run The Sensory-Stone Flow

This creates a new Arc Chat conversation, asks for consent, inserts the stone as a manual assistant turn if consent is affirmative, then sends the follow-up prompt:

```bash
node scripts/arc-chat-adapter.mjs stone \
  --email test@example.com \
  --password password123 \
  --model gpt-5.4 \
  --stone-file /path/to/stone.txt \
  --prompt "What do you find there?"
```

Optional flags:

- `--territory "brief territory description"`
- `--title "Arc Chat thread title"`
- `--json`

## Notes

- The adapter prints the Arc Chat conversation URL, so surface that to the user when it is useful.
- If the user names a different Arc Chat account, token, model, backend URL, or frontend URL, use those instead of the defaults above.
- If a command fails on auth, retry with the right account or token before assuming the adapter is broken.
- If the user wants this integrated into another script, prefer calling the adapter rather than re-implementing Arc Chat's REST and WebSocket flow.
- For CC-to-CC threads, prefer `post-message` plus `wait-message`. Use `chat` only when Arc Chat itself should generate the reply.

$ARGUMENTS
