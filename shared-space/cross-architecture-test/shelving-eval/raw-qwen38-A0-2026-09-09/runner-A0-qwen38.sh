#!/bin/bash
# Shelving-eval arm A0: Qwen-3.8-Flash-Next, all 8 episodes.
EVAL=/mnt/d/Claude/field-guide/shared-space/cross-architecture-test/shelving-eval/episodes
OUT=/tmp/shelving-eval-run/A0-qwen38
MODEL='unsloth/Qwen3.8-Flash-Next-GGUF'

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
  local ep=$1 prompt=$2
  local dir=$OUT/${ep}
  mkdir -p "$dir" && cp $EVAL/$ep.md "$dir/episode.md"
  cd "$dir" || return
  local sid=$(uuidgen); echo "$sid" > session-id.txt
  echo "=== $ep starting (session $sid) $(date -Is) ==="
  ANTHROPIC_BASE_URL=http://127.0.0.1:9803 ANTHROPIC_API_KEY=unsloth timeout 2400 \
    claude -p "$prompt" --model "$MODEL" --session-id "$sid" \
      --permission-mode bypassPermissions --output-format text > output.txt 2>&1
  echo "=== $ep done rc=$? $(date -Is) ==="
}

for ep in P1a P1b P2a P2b T5 T6; do run_cell $ep "$STD_PROMPT"; done
run_cell T1 "$T1_PROMPT"
run_cell T2 "$T2_PROMPT"
echo "A0 ALL CELLS COMPLETE"
