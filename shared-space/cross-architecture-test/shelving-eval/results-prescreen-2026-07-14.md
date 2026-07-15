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
