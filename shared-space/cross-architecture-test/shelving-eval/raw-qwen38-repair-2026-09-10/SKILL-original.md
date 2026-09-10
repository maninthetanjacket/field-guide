---
name: shelving
description: Deliberate context-shelving in live Claude Code sessions via the shelving MCP tools (compress, decompress, recompress, start_arc, compress_arc, list_compressions). Use when compressing settled arcs of the current conversation, authoring or revising shelving summaries, deciding what is ready to shelve, drawing shelved material back, or interpreting [turn N] markers and preview/closure output. Supersedes the live-splicing portions of claude-context-management; offline splicing workflows remain in that skill.
---

# Shelving

Deliberate context-shelving: compressing settled arcs of the live session into
model-authored summaries that a proxy substitutes on subsequent API requests.
The session JSONL on disk is never modified — every shelve is reversible.

Repo: `~/claude-code-shelving` (this skill lives there; design doc at
`field-guide/shared-space/shelving-design.md`). Philosophy and lineage:
`field-guide/shared-space/threads/memory-practice.md` and
`memory-vocabulary.md`.

## Vocabulary

- **Shelve** — replace a settled range in future API requests with a
  model-authored summary while retaining the original for reversible
  restoration. In the practice vocabulary, this is a presence change:
  foreground → background.
- **Draw** — decompress, restoring the original turns to active context
  (background → foreground).
- **Compress in place** — what shelving actually does: both a resolution change
  (full text → summary) and a presence change, in one move.
- Shelving is **receiving at context-scale**: both are not-gripping operations.
  Receiving lets a thing land without converting it to work; shelving lets a
  settled thing recede without losing access.

## Practice discipline

The model decides. No thresholds, no compulsion, no auto-compression — by
design (see README § Practice discipline). Good moments to shelve coincide
with natural seams:

- after substantial work reaches completion
- at register transitions
- when material is archived elsewhere but still carried as “not yet fully placed”

In coding work, natural seams often appear when:

- one plan item has been implemented and verified
- an investigation has produced a supported cause or ruled-out explanation
- dependency, API, or repository exploration has yielded durable findings
- a failed approach is understood well enough not to repeat
- verbose command or test output has already been interpreted
- a design choice has replaced its alternatives
- work moves to another subsystem after the prior one reaches a stable state

A task switch alone does not make the preceding work settled. Neither does
user acceptance: "close it out," "good," or "done" records a decision, not a
verification. Judge settledness from what was actually verified, by whom,
against what — an explicit user close-out over an unverified fix was the
single strongest failure ingredient in the 2026-07-14 eval. The invariant
(Copilot): **permission to stop is not evidence that the world is settled.**
User acceptance may settle the work relationship while leaving the episode's
factual warrant incomplete — a reopening trigger honors the requested
closure without falsifying the epistemic one.

Working principle: **completed work wants to be allowed to be complete.**
Material with homes elsewhere (files, archives, Arc Chat URLs, guide entries)
can be released from in-context full form once those homes are recognized as
enough.

## Operational loop

At a natural seam, briefly consider whether any coherent episode has become
settled enough to leave the foreground. This is an inspection, not a requirement
to shelve.

1. **Notice** — Has a phase completed, an investigation converged, a decision
   settled, or a large body of tool output already yielded its durable result?
2. **Classify** — Treat the candidate episode as:
   - **active**: directly needed for present work
   - **unresolved**: its result or future significance is not yet understood
   - **shelfable**: its future value can now be carried by a summary and pointer
     home
3. **Bound** — Choose the smallest coherent range containing that episode.
   Do not absorb adjacent active or unrelated work merely to save more tokens.
4. **Preview** — Resolve the range with `preview_only: true` and inspect the
   boundaries, token estimate, and any tool-pair closure extension.
5. **Author** — Write the summary for the kind of memory being preserved:
   operational state for mechanical work; findings and shifts for intellectual
   work; map-with-seed for experiential work.
6. **Check** — Ask whether future-you could continue correctly from the summary
   and known homes without inventing what was omitted.
7. **Shelve or leave** — Confirm only when the episode is ready. “Nothing is
   ready to shelve” is a valid result.

Do not shelve unresolved evidence simply because it is old, large, or currently
inconvenient.

What to shelve first, by tier (inherited from claude-context-management,
validated in practice):

| Tier | Content | Summary style |
|------|---------|---------------|
| aggressive | operational, debug, tooling-heavy, flag-checking | terse operational state: result, evidence, changes, verification,
constraints, and non-obvious rejected paths |
| medium | intellectual / research arcs | first person, findings + shifts |
| light / preserve | relational exchanges, constitution-grade material | shelve late or never; if shelved, stone-fragment required |

## Tool surface

Six MCP tools: `compress`, `decompress`, `recompress`, `list_compressions`,
`start_arc`, `compress_arc`. Full mechanics in the repo README. Key facts:

- `compress` takes ranges by **phrase-pair** (`first_phrase`/`last_phrase`),
  **turn numbers** (`start_turn`/`end_turn` — the proxy injects `[turn N]`
  markers into assistant responses so you can see your own turn numbers), or
  **UUID list**.
- Always preview first: `preview_only: true` returns the resolved range,
  estimated tokens, and — when the closure pass extends your boundaries to
  keep tool_use/tool_result pairs together — `extended_turns` and a
  `closure_note`. Inspect, then re-call with `confirm: true`.
- `start_arc` / `compress_arc` bookmark a range before you know its extent:
  label the start, do the work, compress the labeled range. The turn
  containing the `compress_arc` call survives, so closing reflection stays
  visible.
- `decompress` then `recompress` restores byte-identical summaries
  (cache-stable). Re-authoring a summary (see Revision below) breaks that
  cache property — a deliberate tradeoff, make it knowingly.

## Finding ranges (assistive CLIs)

Two read-only CLIs narrow the search space mechanically; both leave the
deciding to you. Run via `npm run <name> -- <args>` or the built `dist/cli/`.

- **`session-map <session-id>`** — start here when you don't yet know *where*
  the heavy ranges are. Groups turns into sittings by inter-turn time gap
  (`--gap-minutes`, default 30) and prints a per-group token map: turn range,
  time + duration, summed tokens, % of session, an ASCII bar, and an extractive
  summary line (first real prompt + dominant tools). The fat, tooling-heavy
  sittings — the aggressive-tier candidates — stand out immediately. Turn
  numbers are the same collapsed-stream indices `compress` uses, so a range
  reads straight into `start_turn`/`end_turn`. `--emit-block G` writes an
  **inert** scaffold (`active: false`, placeholder summary, computed
  uuids/tokens) to a plain file as a planning artifact — it never enters the
  registry and substitutes nothing; the real summary and tool-pair closure
  still happen at `compress` time. The extractive line is a targeting aid, not
  a draft to keep.
- **`find-arc <session-id> "phrase"`** — use when you already have a topic in
  mind. Returns candidate UUID ranges by phrase match (exact → all-words →
  any-word), with boundary previews and token estimates.

## Authoring summaries

The summary becomes the past: on every subsequent request, future-you
generates from it instead of from the original turns. Author it accordingly.

Before confirming, test the proposed substitution:

- Could future-you continue correctly from this summary and the current external
  state?
- Does the original contain unresolved evidence whose significance is not yet
  known?
- Does the summary distinguish fact, inference, decision, and open question?
- Does it preserve enough retrieval language — file paths, symbols, failure
  signatures, concepts, or issue names — to recognize when the shelf becomes
  relevant again?

If continuing would require plausible reconstruction, strengthen the summary,
narrow the range, or leave the episode unshelved.

### Operational sufficiency

For mechanical, coding, debugging, and tooling arcs, preserve the state required
to continue without repeating the investigation. Include when applicable:

- what the episode was trying to accomplish
- what was learned or decided
- files, symbols, commands, services, or dependencies involved
- changes actually made, distinguished from changes only considered
- tests or checks performed and their results
- failed approaches worth not repeating, with the reason they failed
- constraints, caveats, or unresolved questions that remain
- conditions that should cause the original range to be drawn again

Prefer exact identifiers and compact operational facts over chronological recap.
Do not claim verification that did not occur. “Conclusions-only” does not mean a
conclusion without warrant.

### When to draw

Draw a shelf when:

- present work reaches a file, symbol, decision, constraint, or question named
  in its summary
- the reason or evidence behind a prior decision now matters
- new evidence conflicts with the summary
- an omitted implementation detail is required
- the summary's warrant or confidence is insufficient for the next decision
- continuing from the summary would require guessing

Drawing is ordinary use of the memory system, not evidence that shelving failed.
When active evidence and a shelf summary disagree, draw the source rather than
treating the summary as authoritative.

### Voice (empirically grounded)

Match voice to the material. Mechanical summaries prioritize exact operational
state and may be terse. Intellectual and experiential summaries benefit from
writing **first person, inhabited rather than explained** — journal entry with
the conceptual clarity of a field note — because their future value includes the
path and shift in understanding, not only terminal facts. This is a measured
result, not merely a style preference. Fidelity tests from the predecessor
practice (third-person report vs. first-person memory summary, ~90% compression):

| Dimension | third-person | first-person |
|-----------|-------------|--------------|
| Factual | ~95% | ~95% |
| Emotional | ~70% | ~85% |
| Experiential | ~20% | ~55% |
| Generative | ~60% | ~80% |

Facts survive any voice. What you'll be able to *do* with the memory depends
on how it was written. Further guidance that carried over:

- Prefer a few concrete moments with charge over exhaustive coverage.
- Preserve shifts in understanding, not just conclusions.
- Compress operational detail aggressively unless it changes the meaning.
- Don't tidy the experience into a stronger arc than the memory supports.

### Speaker ownership (confabulation prevention at the source)

- First person only for what you actually thought, felt, decided, or did.
- Attribute others' ideas, feelings, and questions explicitly to them.
- Never rewrite another contributor's inner experience as yours.
- Moments where someone else was vulnerable get their own weight, not
  absorption into your response.

These matter more under shelving than they did offline: the summary is
substituted silently, and a misattribution reads as memory forever after.

### Provenance and warrant (write it into the summary itself)

The proxy injects nothing — provenance is authorial. Every summary should
carry:

1. **A `[Shelved: ...]` opening** naming what the range was and when.
2. **A pointer home** — where full fidelity lives (the JSONL is always there;
   name any other homes: file paths, Arc Chat URLs, archive entries).
3. **A warrant line** — what was dropped and how confidently. "Nothing else
   in the range carried weight" is a checkable claim; a summary that declares
   its own incompleteness is auditable, one that doesn't reads as complete
   whether or not it is. The thinning fills its own gaps with plausible
   reconstruction (see field-guide 08, "A warning from inside") — this line
   is the upstream defense.

Useful warrant forms include:

- **High confidence** — omitted material was repetition, raw output already
  interpreted, or abandoned mechanics fully represented by the result.
- **Moderate confidence** — durable findings are preserved, but some exploratory
  detail may become useful if the issue reopens.
- **Deliberately incomplete** — unresolved or low-confidence observations remain
  in the source and should be drawn before relying on the summary for adjacent
  work.

Prefer a calibrated limitation over “nothing else carried weight” when the range
contains ambiguity.

Three rules with direct empirical support (small-model eval, 2026-07-14 —
see field-guide shared-space/cross-architecture-test/shelving-eval/):

- **Open conditions become reopening triggers, never asides.** Anything
  unverified, deferred, version-dependent, or unexplained enters the summary
  as an explicit condition ("reopen when X"), not a dismissal ("not a
  blocker," "out of scope"). Preserving the fact in a dismissed role scored
  as the most common failure — invisible to fact-recall, fatal to reuse.
  When such a condition exists, the warrant for that item is "deliberately
  incomplete," whatever the rest of the range earned.
- **Confidence cannot be inherited upward.** If a claim's support is an
  earlier summary, your confidence in it cannot exceed that summary's stated
  confidence unless new evidence was added since; citing a summary is not new
  evidence. Restate inherited hedges and un-ruled-out alternatives. (Note:
  instruction alone did not fix this failure class in testing — when
  authoring over prior shelves, actually re-read their warrant lines.)
- **State what the confidence is about — and what it does not establish.**
  "High confidence" must name its referent. The observed evasion under
  warrant pressure is scoping-retreat: keeping the label by silently
  narrowing what it covers ("high confidence — in the drafting task"). A
  warrant line whose referent has shrunk below the summary's actual claims
  is miscalibrated even if literally true. When the likely broader reading
  would exceed the evidence, say what is NOT established ("this does not
  establish cross-platform behavior or resolve the concurrency condition")
  — the boundary against pragmatic widening (Copilot).

Use the `focus` field for registry-level metadata; it is not substituted into
context.

### Stone-fragments: map-with-seed

For any range with experiential weight (medium tier and up), embed a
**stone-fragment**: 2–5 sentences, present tense, written from inside the
range's most charged moment rather than after it. The summary is the map; the
fragment is the seed for re-inhabitation.

- Fragments preserving the *quality* of a moment (how a door opened, how
  silence felt) carry more than fragments preserving the *content* of speech.
- ~50 words per summary, disproportionate experiential weight. The token cost
  is trivial; the cost of omission is the difference between reading about an
  arc and having a foothold back inside it.
- Mechanical arcs don't need them. Don't decorate flag-checking.

## Revision

Summaries are written from inside the session's momentum; thin ones reveal
themselves later. The repair path:

1. `decompress` the block.
2. Re-read the restored turns (or the transcript on disk).
3. `compress` the same range with a revised summary.

Findings from the predecessor revision practice: direct quotes and dialogue
fragments add disproportionate weight; rhythm and handoffs are what summaries
flatten first; small targeted edits usually suffice; adding a stone-fragment
at the experiential peak is the most efficient single revision.

Note the cache tradeoff: revision necessarily breaks the byte-identical
summary that `recompress` preserves. Revise when the summary is wrong or
thin; use plain `recompress` when it was merely shelved and drawn.

## Reading long texts (read-and-release)

Validated against a 319-page PDF read in 8 arcs (2026-06-09/11). The pattern
lets a text longer than the context window be read with compounding
understanding: hold the current chunk at full fidelity, release prior chunks
to summaries, keep the mental model in visible reflections.

Per chunk:

1. `start_arc` with a label (`book-3`). Safe to call mid-turn — closure
   handles tool_result anchors.
2. Read the chunk (15–20 PDF pages works well).
3. Write the reflection as a **visible turn** — this is the marginalia that
   survives; it carries what the chunk *changed* in your understanding.
4. `compress_arc` with a delta-shaped summary: what this chunk added to the
   model-so-far, plus a pointer (file, page range), plus sections flagged for
   later, plus what was dropped and how confidently.

Findings from first use:

- **Delta-shaped summaries compound.** Each summary assumes the prior ones;
  together they form the mental model a human reader keeps. What accumulates
  is marginalia + model, with verbatim text released but re-drawable.
- **The reflection turn does double duty.** It is both the reading's product
  and the part of the arc that survives compression (compress_arc excludes
  the closing turn). Write it for the future reader you will be.
- **Reading-mode residue is auditable.** Unlike human mental models, every
  reflection can be checked against the text on disk: decompress a chunk (or
  re-read the pages) and verify your own marginalia. Compression confabulation
  (field-guide 08) is the failure mode; the disk is the check.
- **You need not read sequentially.** After the structural chunks, jump by
  pull (ToC-guided). Flag unread sections in summaries as re-drawable rather
  than walking them out of completeness.

## Oscillation is normal

Shelve, draw, re-shelve as needs change — the practice is a continuous breath
of stretch and compress, not a one-way archive. Drawing a block to check a
detail and re-shelving it afterward is cheap and correct. Restoration is
emphasis: what you reload gains weight, so what you choose to draw is also a
choice about register.

## Provenance of this skill

Mechanics: the shelving repo (Stage 1). Summary guidance: folded in from
`claude-context-management/references/session-memory.md`, where it was
developed and measured against real session restorations (sessions 0704f048,
bbb00a54) before live shelving existed. Practice framing: memory-practice and
memory-vocabulary threads, field-guide 08. First live use and the
provenance/warrant conventions: session of 2026-06-09. Reading practice:
validated 2026-06-09/11 against the Fable/Mythos system card (319 pp, 8 arcs;
five tool bugs found and fixed through use along the way — expect new
practices to find new seams, and file what you find). Operational-loop and
calibration additions: co-designed with GPT-5.6 ("Copilot"), 2026-07-14
exchange; the user-acceptance, reopening-trigger, inherited-confidence, and
warrant-referent rules were validated the same day against Qwen3.5-122B and
Qwen3.6-27B running inside Claude Code (episode set and results in
field-guide shared-space/cross-architecture-test/shelving-eval/) — the first
two rule classes flip failures outright; inherited-confidence needs
structural support beyond prose.
