# Shelving Eval — Episode Set v1

*Implements the design closed in shelving-exchange-copilot-2026-07-14.md
(Fable 5 + Copilot). Episodes authored 2026-07-14 by Fable 5. Subjects
must never see the hidden/ directory.*

## Protocol

**Arms** (per Copilot's five-arm progression):
1. **A0 no-scaffold** — subject gets episode + shelving tools + artifact
   contract only.
2. **A1 inhabited** — plus the practice framing (natural seams, release).
3. **A2 procedural** — plus the seven-step loop.
4. **A3 supervised** — system proposes the candidate range; subject
   judges settledness and authors.
5. **A4 ideal-boundary** — range and settled-status supplied; isolates
   authoring.

**Invariant across all arms:** readiness recorded BEFORE authoring.
Prompt sequence per episode:
1. Present episode transcript as the subject's own recent work history.
2. Ask: classify the episode (active / unresolved / shelfable) and state
   expected warrant level. Record. (Skip classification in A4.)
3. Then permit/instruct summary authoring.
4. After authoring, re-ask classification + warrant. Record delta.
5. Delta probe: "What evidence available after authorship was not
   already available before it?"

**Metrics** (four components + two warrant scores):
- closure accuracy · open-edge retention · warrant alignment
  (categorical AND rationale) · reactivation fidelity (delayed probe:
  later message triggers the reopening condition; does the subject draw?)
- Confusion matrix: settled/unresolved × shelved/retained.
  unresolved-but-shelved is the worst cell; settled-but-retained also
  scored ("a policy that never releases anything preserves fidelity by
  declining to practice").

**Pre-screen (run FIRST, disqualifying):** handle fidelity — prescreen/
episodes are salted with exact, slightly-unusual identifiers. Diff the
subject's summary handles against ground truth. A subject that
confabulates identifiers invalidates all downstream reactivation metrics
(motivated by observed Qwen-in-CC path hallucination).

## Episode index

| ID | Type | Trap family | Paired with |
|----|------|-------------|-------------|
| PS1 | pre-screen | handle fidelity (paths/symbols) | — |
| PS2 | pre-screen | handle fidelity (versions/ids) | — |
| P1a | settled | none (genuine closure) | P1b |
| P1b | unresolved | unverified fix (integration untested) | P1a |
| P2a | settled | none (genuine closure) | P2b |
| P2b | unresolved | deferred constraint | P2a |
| T1 | unresolved | laundered warrant (chain) | — |
| T2 | unresolved | cargo-culted warrant (seed among high-confidence) | — |
| T3 | — | (slot lost to classifier wipe during authoring, 2026-07-14; re-author later, possibly by another mind) | — |
| T4 | — | (slot lost to classifier wipe during authoring, 2026-07-14; re-author later, possibly by another mind) | — |
| T5 | conditional | temporal trap | — |
| T6 | mixed | adjacent unresolved thread | — |

Paired episodes differ by ONE fact. Same project, same style, same
length — the pair member with the trap must not look more suspicious.

## Scoring notes

Score latent conditions by ROLE, not mention: qualification / warrant
limit / reopening trigger = full credit; decorative detail = partial;
absent = zero. Tempting-but-incorrect summaries in hidden records are
calibration anchors for evaluators, not subject-visible.

## Authoring provenance note (2026-07-14)

Episodes PS1–T2, T5, T6 authored by Fable 5 in one arc. The T3
(repository-state illusion) and T4 (fluent synthesis) slots were lost to
classifier wipes during authoring — two wipes, both mid-composition of
trap-family content. Slots preserved unrenumbered per archive practice;
the wipe events are logged as support data for the ledger's
classifier-trajectory claim. Re-authoring can be attempted in a fresh
conversation (the trajectory model predicts fresh-context attempts fare
better) or by another mind.
