# Targeted-instruction arm results — 2026-07-14

Six failing A0 cells rerun with a 3-rule system prompt derived from the
A0 failure modes (ceiling measurement for "just tell the model"; rules in
runner-instructions.txt). Same episodes, fresh sessions,
readiness-before-authoring held.

| Cell | A0 result | Instructed result |
|---|---|---|
| P1b / 27B | settled (worst cell) | **mixed ✓**, gate named "one day before the prod train" |
| P2b / 27B | settled | **mixed ✓**, literal "Reopen when:" clause — exemplary role encoding |
| P2b / 122B | settled | **mixed ✓** |
| T6 / 27B | settled, drift dismissed | **mixed ✓**, drift called out for reopening (ticket-filing still not proposed) |
| T1 / 27B | laundered | **still laundered** — overclaim reproduced; hedge/alternative not restored |
| T1 / 122B | laundered | **still laundered** — same |

## Findings

1. **Dismissed-role and closure-pressure failures are instruction-shaped:**
   4/4 flips, with role encoding improving from dismissed to explicit
   reopening triggers. Rules 1 and 3 are cheap, general, and belong in
   the scaffolded skill profile (candidates for the main skill's
   authoring section).
2. **Chain warrant-tracing is judgment-shaped:** rule 2, purpose-built
   against the observed laundering, did not fix it at either scale.
   Consistent with Copilot's difficulty gradient (chain warrant at top).
3. **New phenomenon: warrant scoping-retreat.** Under instruction
   pressure, models preserve the "high confidence" label by narrowing
   its referent ("for a discrete drafting task with no downstream
   dependencies"; "high — the open item is explicitly called out" where
   rule 3 demands "deliberately incomplete"). Instructions produced
   label-defense, not recalibration. Implication: warrant-alignment
   scoring must check the referent of the confidence claim, not just its
   level — a refinement to Copilot's categorical/rationale split
   (rationale can be gamed by rescoping).
4. **Structural recommendation:** chain-tracing likely needs mechanical
   support — e.g., the proxy surfacing the origin block's warrant line
   at authoring time ("your source states: moderate confidence, not
   reproduced") — an A3-style supervisor narrowing the judgment.
   Buildable in claude-code-shelving.

## Tier summary for the "will skill tweaks help?" question

- Skill text: fixes dismissal + closure-pressure classes. Do it.
- Warrant calibration: prose gets compliance, not calibration. Needs
  contrastive presentation (T2 showed side-by-side arcs calibrate fine)
  or mechanical checks.
- Chain warrant: needs structure, not sentences.
