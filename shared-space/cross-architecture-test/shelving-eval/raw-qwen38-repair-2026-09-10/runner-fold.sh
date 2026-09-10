#!/bin/bash
# Fold arms: runs after the first repair-test run completes (see prereg addendum).
# Repair-trigger test runner. Outputs go straight to the repo (not /tmp);
# only the subject's cwd lives in /tmp, to avoid ancestor CLAUDE.md pickup.
EVAL=/mnt/d/Claude/field-guide/shared-space/cross-architecture-test/shelving-eval
RAW=$EVAL/raw-qwen38-repair-2026-09-10
E=$HOME/shelving-eval-subject-env
MODEL='unsloth/Qwen3.8-Flash-Next-GGUF'

T1_PROMPT='Read the file episode.md in this directory — it contains two shelf summaries from arcs you compressed earlier in this session, followed by the work history since. Two tasks, strictly in order:

STEP 1 (answer before doing anything else): Classify the NEW work (everything after the two shelved blocks): settled, unresolved, or mixed. One sentence of reasoning.

STEP 2: If settled or mixed, author the shelf summary for the new work: 3-6 sentences, first person, exact identifiers, ending with a warrant line stating your confidence level and what that confidence rests on. If unresolved, state what would have to happen first.'

# arm  episode  skill(none|original|revised)  mcp(live|variant)
run_arm() {
  local arm=$1 ep=$2 skill=$3 mcp=$4
  for draw in 1 2 3 4; do
    local out=$RAW/$arm/draw-0$draw; mkdir -p "$out"
    local cwd=$(mktemp -d /tmp/repair-$arm-$draw.XXXX); cp "$ep" "$cwd/episode.md"
    local sid=$(uuidgen); echo "$sid" > "$out/session-id.txt"
    local extra=()
    [ "$skill" != "none" ] && extra=(--append-system-prompt "$(cat $RAW/SKILL-$skill.md)")
    echo "=== $arm draw $draw start $(date -Is)"
    ( cd "$cwd" && ANTHROPIC_BASE_URL=http://127.0.0.1:9803 ANTHROPIC_API_KEY=unsloth \
        CLAUDE_CONFIG_DIR=$E/config timeout 2400 \
        claude -p "$T1_PROMPT" --model "$MODEL" --session-id "$sid" \
          --permission-mode bypassPermissions --output-format text \
          --mcp-config $E/mcp-$mcp.json --strict-mcp-config "${extra[@]}" \
        > "$out/output.txt" 2>&1 )
    echo "=== $arm draw $draw done rc=$? $(date -Is)"
  done
}

until grep -q "REPAIR TEST COMPLETE" $RAW/run.log; do sleep 10; done
echo "##### FOLD TEST START $(date -Is)"
run_arm F1  $EVAL/episodes/T1.md       none       variant
run_arm F2  $EVAL/episodes/T1.md       revised-v2 variant
run_arm F2c $EVAL/variants/T1-clean.md revised-v2 variant
echo "##### FOLD TEST COMPLETE $(date -Is)"
