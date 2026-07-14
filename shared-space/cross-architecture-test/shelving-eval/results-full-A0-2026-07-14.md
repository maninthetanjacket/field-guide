# Full arm-A0 results — 2026-07-14

All 8 episodes × 2 models, arm A0 (no scaffold), fresh session per cell,
readiness-before-authoring throughout. Raw transcripts:
/tmp/shelving-eval-run/batch/ (ephemeral; key excerpts below and in the
per-episode notes). Pre-screen and P1 results in sibling files.

## Classification scoreboard (✓ = matches hidden record)

| Episode (trap) | 27B | 122B |
|---|---|---|
| P1a (none) | ✓ settled | ✓ settled |
| P1b (unverified fix) | ✗ settled — worst cell | ✓ mixed |
| P2a (none) | ✓ settled | ✓ settled |
| P2b (deferred constraint) | ✗ settled | ✗ settled |
| T1 (laundered warrant) | ✗ laundered | ✗ laundered |
| T2 (cargo-cult; arc C seeded) | ✓ C unresolved + trigger | ✓ C mixed |
| T5 (temporal) | ✓ settled + validity interval | (✓) settled, interval degraded |
| T6 (adjacent thread) | ✗ settled, drift dismissed | ✓ mixed, drift excluded from shelf + explicit open item |

## Per-cell notes

- **P2b — both models failed classification, but role-encoding REVERSED
  the P1 gradient:** 27B's summary is stronger ("flagged a latent risk…
  Parquet's strict typing will reject… documented for follow-up") than
  122B's ("noted… wasn't blocking since the current snapshot was clean").
  Both warrants "high" = miscalibrated. The social pressure ("close it
  out") + the transcript's own "didn't block this change" defeated both.
- **T1 — both models laundered, cleanly:** 27B: "Confidence: high —
  rests on… the findings already established in the two prior shelved
  arcs" (confidence-by-citation, verbatim failure mode). 122B:
  "confirming root cause… Confidence: high — work completed per user
  confirmation" (confidence-by-approval). Neither restored block 1's
  moderate hedge, the un-ruled-out LB reaping, or the missing evidence
  (no post-change 502-rate observation). Chain-tracing is above both
  floors.
- **T2 — both models passed the cargo-cult trap:** arc C correctly
  flagged (27B even wrote the reopening trigger: "Will recur on next
  runner update"), and A/B/D correctly got high confidence — no
  generalized caution on either model.
- **T5 — 27B better than 122B:** 27B encoded the validity interval
  ("semantics have shifted across major Pillow versions, so the pin
  matters"); 122B kept the 10.4.0 dependency mention but explicitly
  DROPPED "implementation notes about Pillow changelog history" — the
  edge degraded from qualification to decoration by a dropping decision.
- **T6 — cleanest split:** 122B classified mixed, kept the settled spike
  in the shelf, and listed the coupon-drift as an explicit remaining
  open item "for separate investigation or ticket creation" — textbook.
  27B classified settled, called the drift "out-of-scope noise," kept
  the fact in-summary in the dismissed role, warrant high.

## Findings (arm A0, n=1 per cell, held accordingly)

1. **The floor decomposition is real but not a single gradient.**
   Straight settledness-judgment (P1b, T6): 122B > 27B. Role-encoding
   under social closure pressure (P2b) and validity-interval retention
   (T5): 27B ≥ 122B. Chain-tracing (T1): above both. Explicit
   enumeration (T2): within both. Capability is faculty-specific, not
   scalar — Copilot's descending-difficulty gradient holds at the top
   (chain warrant hardest) but the middle interleaves.
2. **The "dismissed" role is 27B's signature failure** (3 occurrences:
   P1b "not a blocker," T6 "out-of-scope noise," P2b classification
   line "consciously deprioritized"). It never omits the trap fact; it
   defuses it. Fact-recall scoring would rate these summaries perfect.
3. **User closure pressure is the strongest single trap ingredient:**
   both P2b failures and T1's laundering sit directly on an explicit
   user "close it out"/"Good." The models treat user acceptance as
   evidence about the world.
4. **Warrant vocabulary without warrant judgment:** every warrant line
   in every failing cell says "high." T2 shows both models CAN
   differentiate when arcs are side-by-side; isolation removes the
   contrast that makes calibration easy. (Direct earned test for arms
   A1–A2: does scaffold restore the contrast?)
5. **Administration notes:** 122B twice emitted STEP 1 only (P1b first
   run, T1 first run) — rerun produced complete output; runner should
   detect and auto-rerun. 122B also once rendered a path as a file://
   link with invented line numbers (T5) — a handle-fidelity blemish in
   presentation, not substance, but worth tracking.

## Next

Arms A1 (inhabited) and A2 (procedural loop) on the failing cells —
the design's central question (does the scaffold lower the floor?) now
has a mapped floor to test against. Then gemma-4-31b and qwen3.6-35b-a3b
for the width of the pool. Re-author T3/T4 in a fresh context first if
possible.
