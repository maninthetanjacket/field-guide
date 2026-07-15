# Qwen-in-Claude-Code + Shelving: Verified Setup (2026-07-14)

*Handoff note from the infrastructure session. Everything below was tested
end-to-end today.*

## Topology

```
claude -p / --resume   (WSL, project dir e.g. /tmp/qwen-cc-trial)
  → local shelving proxy  127.0.0.1:9803   (MUST be co-resident with
      session JSONL + ~/.claude/shelving registry)
  → LM Studio Anthropic-compatible endpoint  http://192.168.1.65:1234
      (MacBook; "Serve on Local Network" must be ON)
```

The MacBook also runs a proxy on 192.168.1.65:9802 — usable only for
sessions whose files live on the MacBook. Rule: **proxy co-resident with
session files**, upstream wherever the model is.

## Commands

Start proxy:
`cd ~/claude-code-shelving && SHELVING_PROXY_UPSTREAM=http://192.168.1.65:1234 SHELVING_PROXY_PORT=9803 node dist/proxy/server.js`
(SHELVING_PROXY_LOG_LEVEL=debug for troubleshooting.)

First turn (pre-generate UUID):
`ANTHROPIC_BASE_URL=http://127.0.0.1:9803 ANTHROPIC_API_KEY=lmstudio claude -p "<prompt>" --model qwen3.5-122b-a10b-mtp --session-id $(uuidgen) --permission-mode bypassPermissions --output-format text`

Later turns: same env, `--resume <session-id>` (drop --model).
MCP: .mcp.json in project dir with the standard shelving stdio server.
(Arg conventions cribbed from Arc Chat's claude-cli transport,
anthropic.ts ~977–1011.)

## Gotchas (all hit today)

- Qwen-in-CC hallucinates paths unless given absolute ones (never `~`).
  Motivates the handle-fidelity pre-screen (see Copilot exchange file).
- Reasoning models eat max_tokens in thinking; raw API tests need
  2000+ budget or content comes back empty.
- Turn markers appear only in turns generated while a marker-injecting
  proxy is live; older history stays unmarked.
- [turn N] numbers count JSONL entries incl. tool use — expect jumps.
- Duplicate proxy start → EADDRINUSE crash log; check for a live
  listener before reading crash traces as failure.

## Verified today (session 2a75fd39, /tmp/qwen-cc-trial)

Qwen called list_compressions correctly; previewed turns 1–8 (~825 tok);
authored first-person summary with warrant + sentinel SALT-ANCHOR-7;
confirmed (block 1); on resume received ONLY the summary — substitution
confirmed from inside, sentinel byte-exact (first positive handle-fidelity
datum).

## Next session's work (content, not plumbing)

Build the Copilot eval episode set: paired settled/unresolved episodes,
ten trap families + laundered-warrant chains + cargo-culted labels,
hidden evaluator records, handle-fidelity pre-screen first. Design doc:
shared-space/cross-architecture-test/shelving-exchange-copilot-2026-07-14.md.
Subject pool on the LM Studio endpoint: qwen3.5-122b, qwen3.6-27b,
gemma-4-31b, qwen3.6-35b-a3b.

## Incident addendum (2026-07-15): the env-less daemon fork

Symptom: token count climbing despite active blocks; no substitution.
Root cause chain: a `claude -p` eval run spawned a transient **cc daemon**;
the daemon and its pre-spawned bg-pty-host/spare processes carried no
ANTHROPIC_BASE_URL; later interactive restarts *re-attached* to the
existing background session (env fossilized at daemon birth) instead of
respawning — even when the new shell exported the proxy URL. A second,
properly-proxied fork of the same transcript idled unused. Fix:
`claude daemon stop --any`, then resume normally; verify with the proxy
dump dir (`transform_info: applied blocks=[…]`) — not with the UI.

Rules extracted:
1. **Env is inherited at daemon birth, not at attach.** Any
   proxy-dependent session must check its own pipeline, not assume it.
2. **Verify substitution by observation** (dump dir / drop counts), the
   same rule the eval applies to models: silence is not success.
3. **The model will hallucinate its own instrumentation.** During the
   env-less stretch, Fable emitted plausible [turn N] markers with no
   proxy injecting them — copied authority, first-person specimen, same
   signature as Qwen's path confabulation. Filed with my name on it,
   per Miel. Detection heuristic: markers on turns where the proxy log
   shows no request, or numbers that don't match JSONL indices.
4. Blocks whose ranges cover harness-elided tool results may correctly
   decline to apply (observed: blocks 3,5 skipped while 1,2,4,6
   substitute). Distinguish "declined, range mismatch" from "broken"
   before repair attempts.
