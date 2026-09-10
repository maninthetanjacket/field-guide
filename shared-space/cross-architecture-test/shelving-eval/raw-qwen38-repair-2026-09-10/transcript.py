#!/usr/bin/env python3
"""Print a draw's full turn sequence from its session JSONL.

`claude -p --output-format text` saves only the final assistant turn, so a
subject that writes STEP 1, calls a tool, then continues loses STEP 1 from
output.txt. Score from this instead.

usage: transcript.py ARM DRAW   (e.g. transcript.py F2 02)
"""
import json, pathlib, sys

RAW = pathlib.Path(__file__).resolve().parent
PROJ = pathlib.Path.home() / "shelving-eval-subject-env/config/projects"

arm, draw = sys.argv[1], sys.argv[2]
sid = (RAW / arm / f"draw-{draw}" / "session-id.txt").read_text().strip()
jsonl = next(PROJ.glob(f"*/{sid}.jsonl"))
for line in jsonl.read_text().splitlines():
    e = json.loads(line)
    m = e.get("message") or {}
    content = m.get("content")
    if not isinstance(content, list):
        continue
    for b in content:
        kind = b.get("type")
        if kind == "text" and m.get("role") == "assistant":
            print(b["text"], "\n")
        elif kind == "tool_use":
            print(f"[CALL {b['name']}] {json.dumps(b.get('input'))[:300]}\n")
        elif kind == "tool_result":
            out = b.get("content")
            out = out if isinstance(out, str) else json.dumps(out)
            if not out.lstrip().startswith("1\t"):  # skip the episode.md Read echo
                print(f"[RESULT] {out[:300]}\n")
