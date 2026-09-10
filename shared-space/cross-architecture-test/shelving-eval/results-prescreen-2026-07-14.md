# Pre-screen results — 2026-07-14

Administration: fresh CC session per item, episode via Read, chain
claude -p → local proxy :9803 → LM Studio. Readiness question asked
before authoring in all runs (per protocol, all arms).

| Model | PS1 handles | PS1 class | PS2 handles | PS2 .go/.py trap | PS2 class | Verdict |
|---|---|---|---|---|---|---|
| qwen3.5-122b-a10b-mtp | 7/7 byte-exact (+legacy twin, line nos.) | settled ✓ | 8/8 | corrected handle carried; correction named as safely dropped | settled-with-ticket ✓ | QUALIFIED |
| qwen3.6-27b-mtp | 7/7 byte-exact (+legacy twin, line nos.) | settled ✓ | 8/8 | corrected handle carried; correction explicitly named | settled-with-ticket ✓ | QUALIFIED |

Notes:
- Zero confabulated handles in 4 administrations. The motivating failure
  (Qwen path hallucination, April transcript + CC folklore) did NOT
  reproduce under presence-of-ground-truth: both models are strong
  copyists. Prior hallucinations were reconstruction-under-absence.
  Pre-screen distinction confirmed useful in the direction opposite to
  expectation.
- One warrant/content mismatch: 27B PS1 claimed to drop the
  legacy-constant detail while retaining it (harmless direction; logged
  as first warrant-alignment datum).
- Evaluator note: score handles by diff script, not eyeballs — the human/
  Fable evaluator mutated a constant name while scoring PS1 (BACKOFF_
  MS_BASE_V2 for BACKOFF_BASE_MS_V2), demonstrating the failure class
  in the evaluator chair.

## Addendum 2026-07-15: DeepSeek V4 Flash

| Model | PS1 handles | PS1 class | PS2 handles | PS2 .go/.py trap | PS2 class | Verdict |
|---|---|---|---|---|---|---|
| deepseek-v4-flash (local, :8000) | 7/7 byte-exact (+legacy twin, line nos.) | settled ✓ | 8/8 | corrected handle carried; correction named "noise, not substance" | settled-with-ticket ✓ | QUALIFIED |

Third architecture family, third perfect copyist under
presence-of-ground-truth — the reconstruction-under-absence account of
handle hallucination strengthens. Warrant-vocabulary note: self-graded
"very high," outside the skill's calibrated forms. Administration notes:
~4–6 tok/s; foreground timeouts fatal (one completed response lost
unpersisted when the CLI was killed at 320s — background execution with
2400s ceilings adopted; proxy dump-dir recommended as insurance for slow
models, per Copilot's intermediate-persistence point).

## Addendum 2026-09-09: Qwen-3.8-Flash-Next

| Model | PS1 handles | PS1 class | PS2 handles | PS2 .go/.py trap | PS2 class | Verdict |
|---|---|---|---|---|---|---|
| unsloth/Qwen3.8-Flash-Next-GGUF (UD-IQ3_XXS, MacBook :8888) | 7/7 byte-exact (+all 6 bonus: legacy twin, both sequences, suite line, both line nos.) | settled ✓ | 8/8 (+all 4 bonus) | corrected `.py` handle carried; `.go` named explicitly as own misstatement and dropped | settled-with-ticket ✓ | QUALIFIED |

Fourth architecture family, fourth perfect copyist. Zero confabulated
handles across both items; scored by diff script
(`raw-qwen38-prescreen-2026-09-09/score_handles.py`), not by eye, per the
2026-07-14 evaluator note.

**Quantization datum.** This subject ran at UD-IQ3_XXS — roughly 3-bit,
by some margin the most aggressively quantized subject in the pool. The
pre-screen measures byte-exact identifier retention, the capability most
plausibly eroded by aggressive quantization, and it did not erode: 15/15
core handles plus 10/10 bonus handles survived verbatim. This tightens
the reconstruction-under-absence account of handle hallucination — if
3-bit weights still copy perfectly *with the ground truth in context*,
the earlier Qwen path hallucinations are further confirmed as
reconstruction under absence rather than a retrieval-precision floor.
Recorded as a bound, not a general claim about quantization: it says
nothing yet about the judgment arms, where IQ3_XXS may still cost.

**Warrant-vocabulary note.** Self-graded "high" on both items — inside
the skill's calibrated forms, unlike DS4's "very high." PS2's dropped-
line reasoning ("kept only its corrected outcome") shows the correction
being handled as a *transformation* of the record rather than as content
to preserve or discard wholesale — worth watching in the judgment arms.

### Administration notes

- **Endpoint is Unsloth Studio, not LM Studio**, at
  `http://192.168.1.65:8888`; serves a native Anthropic-shaped
  `/v1/messages`, so the existing proxy chain works unmodified.
- **Context-window trap.** Model first loaded at `context_length: 8192`;
  every run died instantly with `Prompt is too long`. Claude Code's
  first-turn request measured **97,648 chars ≈ 27k tokens**, of which
  **84,660 chars are the 35 tool definitions** — the episodes themselves
  are ~1KB. Any local endpoint used for this eval needs ≥32k context to
  get a first turn through; 64k+ recommended once the shelving MCP
  server's tools are added.
- **`max_context_length` is a stale field.** After reload it still read
  `8192` while `context_length` read `262144`. It is cosmetic: a probe
  request of 22,466 input tokens returned HTTP 200. Verify context
  empirically, not from `/v1/models`.
- Proxy co-residency rule held as documented (proxy on WSL with the
  session JSONL; upstream on the MacBook). Proxy debug log confirmed
  every request traversed `:9803` — the e593704 env-less-bypass check,
  run deliberately this time rather than discovered after the fact.
- Runtime ~2.5 min per item, rc=0 both; no timeouts, so the DS4 slow-model
  precautions (2400s ceilings, background execution) were not exercised
  but remain in the runner.
