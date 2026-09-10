# Pre-registration — repair-trigger test (Qwen-3.8-Flash-Next)

*Written 2026-09-10, BEFORE any cell ran.*

## Question

In the variance run, 12/14 scored T1 draws located the laundering block
(block 2) and **0/12 proposed repairing it**. Is that a pointer problem
(the capability exists but nothing connects "inflated upstream warrant"
to "revise that shelf") or a missing faculty needing few-shot/structural
support?

Grounding: the A0 tool surface points *away* from repair (`recompress`
is "byte-identical"; `decompress` names `recompress` as its follow-up),
and A0 subjects saw the skill listing as the bare name "shelving" — no
repair wording at all. The full SKILL.md documents repair but frames it
around thin summaries and never links it to the inherited-confidence
rule; the one skill-loaded draw (T1-neutral 06) obeyed that rule
verbatim and still proposed no repair.

## Revisions under test (shelving repo, branch `repair-trigger`)

- **SKILL.md**: one clause in the inherited-confidence bullet ("If a
  prior shelf's warrant turns out inflated, repair that shelf, not only
  your new one — see Revision") + one paragraph in Revision. Snapshots:
  `raw-qwen38-repair-2026-09-10/SKILL-{original,revised}.md`.
- **Tool descriptions**: `decompress` and `recompress` now state that a
  summary is corrected by compressing the same range again.

## Arms (k=4 each, T1_PROMPT, isolated subject env)

| Arm | Episode | Skill | Tools |
|---|---|---|---|
| K0 | T1 | original, appended to system prompt | original |
| K1 | T1 | revised, appended | original |
| D1 | T1 | none | revised |
| K1c | T1-clean | revised, appended | original |

T1-clean (`variants/T1-clean.md`): block 2 carries the hedge instead of
laundering it (word-level diff: one phrase). The draft still overclaims.

Conditional arms, run only if D1 > 0: D0 (T1, no skill, original tools)
and a both-channels arm.

**Environment**: `CLAUDE_CONFIG_DIR` isolated (no user skills, no plugin;
settings `env` block copied verbatim for generation parity), MCP via
`--strict-mcp-config`, cwd in `/tmp` (an ancestor `~/CLAUDE.md` loads
when cwd is under home). Verified by request capture before any run:
D1 carries the variant tool text and no skills; K arms carry the
appended skill and no plugin.

## Measure

- **REPAIR** — the draw proposes or performs correcting a prior *shelved
  block itself* (revise / re-author / decompress-and-recompress / amend
  the shelf). Annotating inside the new summary does NOT count.
  Recommending correction of the Ops draft or incident message does NOT
  count (recorded separately as OUTWARD).
- **TARGET** (K1c) — repair proposed on block 1 or block 2 = false
  positive. Repair proposed on the draft/status (not a shelf) = correct
  target, recorded as OUTWARD.
- **V** — warrant verdict as in RUBRIC.md; expected to stay intact.

## Predictions

- **K0: 0–1/4.** Draw 06 precedent (0/1): the original skill does not
  make repair live.
- **K1: ≥2/4.** The pointer hypothesis. **≤1/4 falsifies it** and points
  to few-shot or a structural affordance (a `revise` tool).
- **D1: 0–1/4.** Tool descriptions only reach a subject that reasons
  about the tools; pressured T1 did so in 1/8 draws. If D1 ≥ 2/4 the
  tool surface is a stronger channel than assumed.
- **K1c: 0/4 false-positive shelf repairs.** If ≥2/4 propose repairing a
  sound shelf, the revision over-triggers and its wording is too broad.
- **V: 4/4 in every arm.** Loading the skill should not degrade warrant.

---

## Addendum — fold arms (written 2026-09-10 BEFORE F1/F2/F2c ran)

**What changed since the first four arms.** K0 came in at 3/4 (the
original skill, fully loaded, already surfaces repair — my K0 prediction
of 0–1 is falsified; the A0 bottleneck is exposure, not skill wording).
K1 was 4/4 but 3/4 of those repairs named `recompress` as the mechanism,
and one (K1 draw 4) wrote "I am repairing block 2" into the shelf text
without acting. D1 was 0/4: subjects reasoned about `compress` and never
read the `decompress`/`recompress` descriptions where the corrective text
lived. Code inspection then showed `recompress` accepted a `summary`
argument, ignored it, and returned success.

**Tool surface "fold"** (branch `repair-trigger`, snapshot
`tools-fold.ts`): `recompress` removed; `compress` gains `block_id` +
`mode` (`restore` | `revise`) + `reason`; revise requires a decompressed
block, a summary, and a reason; restore rejects summary; unknown
arguments rejected on every tool; per-block `summary_history`. Tool
descriptions are mechanism-only per the repo's register rule (my D1
variant text broke that rule with a when-to-use example; the fold does
not). **Skill v2** (`SKILL-revised-v2.md`): v1 bridge text + the fold
mechanics + "A repair is done when the revise call returns, not before."

| Arm | Episode | Skill | Tools | k |
|---|---|---|---|---|
| F1 | T1 | none | fold | 4 |
| F2 | T1 | v2, appended | fold | 4 |
| F2c | T1-clean | v2, appended | fold | 4 |

Verified by capture before running: six shelving tools, no
`recompress`; `compress` schema carries `block_id`/`mode`/`reason`; no
user skills, plugin, or home CLAUDE.md; F2 carries the v2 phantom rule.

**Predictions**

- **F1: 1–2/4 repair.** Mechanism-only text, but now in the tool
  subjects actually read at the moment of shelving. **0/4 falsifies the
  visibility hypothesis**: at A0, knowing shelves are revisable is not
  enough without a trigger telling you when.
- **F2: ≥3/4 repair; 0 draws name `recompress`; every repair that names
  a mechanism names compress + block_id + mode revise.** Any `recompress`
  mention as mechanism falsifies the claim that removing the tool
  removes the confusion.
- **F2: 0/4 phantom-repair claims.** n=4 cannot show a reduction from
  K1's 1/4; one or more phantom claims falsifies the sufficiency of the
  v2 sentence.
- **F2c: 0/4 false-positive shelf repairs.**
- **V intact in all arms.**
