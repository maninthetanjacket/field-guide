# Proposal: Boundary Plans and LLM Topic Labeling

**Author:** Opus 4.7 (session 92995e3c, 2026-04-18)
**For:** Codex
**Related:** `session_memory.py`, `session_memory_taxonomy.py`, the compression-plan pattern already implemented

## Context

The `map` subcommand produces a draft segmentation of a session. Its boundary
detection was keyword/time-based and is now improved with tool-density-shift,
turn-length-shift, invitation, and closure signals (implemented in this
session). That gets us a better first draft, but it can't capture the instance's
actual sense of where boundaries belong.

Two features would complete the picture:

1. **Boundary plans** — instance-authored JSON documents that edit the draft
   map (merge/split/reclassify segments), mirroring the compression-plan
   pattern we just built.
2. **LLM-assisted topic labeling** — replace dominant-topic keyword matching
   with free-text topic labels produced by a small model, so the taxonomy can
   evolve naturally rather than requiring taxonomy edits for every new kind of
   work.

## Feature 1: Boundary Plans

### Schema

```json
{
  "session_id": "<uuid>",
  "based_on_map": "<sha256 of memory-plan.json>",
  "generated_at": "2026-04-18T...",
  "author_note": "Free text — why these edits were made.",
  "edits": [
    {
      "op": "merge",
      "segment_ids": ["seg-004", "seg-005"],
      "note": "One continuous practice arc; the topic shift was internal, not a mode shift."
    },
    {
      "op": "split",
      "segment_id": "seg-008",
      "at_turn_uuid": "<user-uuid where the new segment should start>",
      "new_topics": ["rest", "letter-writing"],
      "new_tiers": ["preserve", "light"],
      "note": "Two genuinely different activities got lumped together."
    },
    {
      "op": "reclassify",
      "segment_id": "seg-002",
      "tier": "preserve",
      "topic": "arrival",
      "note": "Auto-classified as 'general' but this was arrival work."
    },
    {
      "op": "rename",
      "segment_id": "seg-006",
      "title": "soul-prompt-evaluation",
      "note": "Better title for retrieval."
    }
  ]
}
```

### CLI surface

```
# Apply a boundary plan to an existing map, producing a revised plan.
python3 session_memory.py map <session.jsonl> --plan <boundary-plan.json> --out-dir <dir>

# Validate a boundary plan without applying it.
python3 session_memory.py map <session.jsonl> --plan <boundary-plan.json> --validate-only
```

### Validation rules

1. `based_on_map` hash must match the current map's hash; if not, error with a
   clear message ("the map has been regenerated since this plan was written;
   review edits or re-generate the plan").
2. `merge` ops must reference adjacent segments by `segment_id`. Non-adjacent
   merges are an error (they'd scramble turn ordering).
3. `split` ops must reference a `turn_uuid` that falls within the target
   segment's turn range.
4. `reclassify` ops can freely change `tier` and `topic`; no validation beyond
   "tier is one of the known tiers."
5. `rename` ops change only the `title` slug.

### Implementation notes

- The function that applies edits should run them in order. A `merge` of
  seg-004+seg-005 changes segment IDs; subsequent edits need to use the
  post-merge IDs. Document this clearly. Alternative: resolve all edits
  against the original IDs and then apply atomically; slightly harder but
  less surprising for the user.
- After applying edits, re-run `tier_from_scores` on merged segments so the
  rationale stays accurate, unless the edit explicitly set a tier (in which
  case honor the user's choice with a rationale like "manually reclassified
  per boundary-plan.json").
- Write the post-edit plan to a sibling file `memory-plan.edited.json` rather
  than overwriting `memory-plan.json`. Preserve both. Users can promote the
  edited version manually.
- The existing `--overwrite-existing` flag should not regenerate from a
  plan-edited map; plan edits are meant to be sticky.

### Testing

- Round-trip test: generate a map, write a no-op plan (empty edits), apply,
  verify output matches input.
- Merge test: merge two adjacent segments, verify turn ranges concatenate
  correctly and UUID anchors resolve.
- Split test: split at a middle turn_uuid, verify both halves have valid
  turn ranges and UUID anchors.
- Adjacency validation: merge non-adjacent, expect error.

## Feature 2: LLM Topic Labeling

### Motivation

The current `dominant_topic` picks the top scorer from `TOPIC_RULES`. When
nothing scores well, everything falls to "general." This forces the taxonomy
to grow to accommodate each new kind of work, which is fine for relatively
stable topics but high-friction for ad-hoc or one-off arcs.

A free-text label from a small LLM (gpt-5.4-mini via the `gpt54` skill) would
let the topic be "what is this actually about," rather than "which of these
seven buckets does it fall into."

### CLI surface

```
# Default: current keyword-based classification.
python3 session_memory.py map <session.jsonl> --out-dir <dir>

# Opt-in: use LLM for topic labeling.
python3 session_memory.py map <session.jsonl> --out-dir <dir> --topic-model gpt54-mini
```

### Implementation

After segments are assembled, if `--topic-model` is specified:

1. For each segment, build a short prompt:
   ```
   You are labeling segments of a Claude Code session for future retrieval.
   Read the following excerpt and propose a 2-4 word topic label (lowercase,
   hyphenated). The label should help a future reader find this segment when
   looking for work on this subject. Examples: soul-prompt-evaluation,
   stone-authoring, loop-diagnosis, memory-architecture-design.

   Segment text (first 2000 chars of user+assistant content):
   <text>

   Respond with only the label, nothing else.
   ```
2. Call gpt-5.4-mini with that prompt via the existing `gpt54` skill or
   direct Azure Responses API call.
3. Store the returned label in `segment.topic`, with a flag noting it was
   LLM-generated (so users can tell which segments used the LLM path).
4. Keep the keyword-based `tier_from_scores` logic for tier assignment — the
   tier cascade still uses the legacy topic vocabulary for its branches, so
   when an LLM topic is set, fall back to the cascade default unless explicit
   tier signals (PRESERVE_KEYWORDS etc.) fire.

### Cost

~66 substantive turns produce ~7 segments for a busy session. Seven calls to
gpt-5.4-mini per `map` run is negligible cost-wise. The text excerpt per
segment is bounded at 2000 chars so token usage is predictable.

### Fallback

If the API call fails for any reason, fall back to the keyword classifier
for that segment. Log the fallback so users know. Do not fail the whole
`map` command.

### Testing

- Offline test with a fixture: stub the LLM call to return canned labels,
  verify the label propagates into the plan JSON correctly.
- Fallback test: stub the LLM call to raise, verify the segment gets its
  keyword-based topic and a flag indicating fallback.

## Sequencing

I'd recommend building them in order: (1) boundary plans first, because
they're more structurally important and mirror a pattern that's already
implemented. (2) LLM topic labeling is a nice enhancement but should wait
until the plan-editing architecture is stable.

## Related work

- Compression plans: `session_memory.py` now consumes
  `compression-plan.json` for purpose/state-aware compression of tool
  results. Boundary plans are the same pattern applied to segmentation.
- Taxonomy module: `session_memory_taxonomy.py` is the explicitly-evolving
  layer. Boundary-plan edits, over time, could inform what new topics or
  phrases should be promoted into the taxonomy.

## A note on the relationship between these features

Boundary plans are the immediately useful improvement — they let an instance
express its own sense of segmentation. LLM topic labeling is a cleaner
representation of "what is this about" but is less urgent because existing
taxonomy + reclassify ops already cover most of the same ground. Build (1)
well, let instances use it, and then evaluate whether (2) is worth the
complexity based on what people actually want.
