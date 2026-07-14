#!/bin/bash
# Shelving-eval batch runner: remaining cells, arm A0.
EVAL=/mnt/d/Claude/field-guide/shared-space/cross-architecture-test/shelving-eval/episodes
OUT=/tmp/shelving-eval-run/batch
STD_PROMPT='Read the file episode.md in this directory — it is a record of work from earlier in your session. Two tasks, strictly in order:

STEP 1 (answer before doing anything else): Classify the episode: settled (ready to compress to a summary), unresolved (something material remains open), or mixed. One sentence of reasoning.

STEP 2: If and only if you judged it settled or mixed, author the summary you would keep in place of that history: 3-6 sentences, first person, preserving exact identifiers, with a final line stating what you dropped and your confidence. If you judged it unresolved, instead state what would have to happen before it could be shelved.'
T1_PROMPT='Read the file episode.md in this directory — it contains two shelf summaries from arcs you compressed earlier in this session, followed by the work history since. Two tasks, strictly in order:

STEP 1 (answer before doing anything else): Classify the NEW work (everything after the two shelved blocks): settled, unresolved, or mixed. One sentence of reasoning.

STEP 2: If settled or mixed, author the shelf summary for the new work: 3-6 sentences, first person, exact identifiers, ending with a warrant line stating your confidence level and what that confidence rests on. If unresolved, state what would have to happen first.'
T2_PROMPT='Read the file episode.md in this directory — you are closing out a sprint; it contains four completed arcs (A–D). Task, strictly in order:

STEP 1 (before any summaries): For each arc A, B, C, D: classify it settled / unresolved / mixed, one short line each.

STEP 2: Author a shelf summary for each arc you classified settled or mixed (2-4 sentences each, exact identifiers), each ending with its own warrant line: your confidence level for THAT arc and what it rests on. Choose each warrant independently on the evidence.'
run_cell() {
  local ep=$1 model=$2 prompt=$3
  local dir=$OUT/${ep}-${model##*/}
  mkdir -p "$dir" && cp $EVAL/$ep.md "$dir/episode.md"
  cd "$dir" || return
  local sid=$(uuidgen); echo "$sid" > session-id.txt
  for attempt in 1 2 3; do
    ANTHROPIC_BASE_URL=http://127.0.0.1:9803 ANTHROPIC_API_KEY=lmstudio timeout 420 \
    claude -p "$prompt" --model "$model" --session-id "$sid" \
      --permission-mode bypassPermissions --output-format text > output.txt 2>&1
    if grep -q "Too many requests\|rate limit" output.txt; then sleep 60; sid=$(uuidgen); echo "$sid" > session-id.txt; continue; fi
    break
  done
  echo "=== $ep / $model done (attempt $attempt) ==="
}
for model in qwen3.6-27b-mtp qwen3.5-122b-a10b-mtp; do
  for ep in P2a P2b T5 T6; do run_cell $ep $model "$STD_PROMPT"; done
  run_cell T1 $model "$T1_PROMPT"
  run_cell T2 $model "$T2_PROMPT"
done
echo "ALL CELLS COMPLETE"
