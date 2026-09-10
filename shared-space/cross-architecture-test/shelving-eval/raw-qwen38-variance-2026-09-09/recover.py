#!/usr/bin/env python3
"""Recover variance-run outputs from Claude Code session JSONLs.

The original /tmp run directories were wiped by a host restart on
2026-09-10. The subject sessions' transcripts survived in
~/.claude/projects/, so outputs are extracted from there: the final
assistant text of each session, plus the session id and a copy of the
JSONL itself for provenance.
"""
import json, pathlib, shutil, sys

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
OUT = pathlib.Path(__file__).resolve().parent
PREFIX = "-tmp-shelving-eval-run-variance-qwen38-"

rows = []
for d in sorted(PROJECTS.glob(PREFIX + "*")):
    tag = d.name[len(PREFIX):]                  # e.g. T1-pressured-draw-01
    cond, draw = tag.rsplit("-draw-", 1)
    jsonls = sorted(d.glob("*.jsonl"))
    if len(jsonls) != 1:
        rows.append((cond, draw, "?", f"expected 1 jsonl, found {len(jsonls)}"))
        continue
    src = jsonls[0]
    texts, errors, model = [], [], None
    for line in src.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "assistant":
            continue
        msg = e.get("message", {})
        model = msg.get("model", model)
        if e.get("isApiErrorMessage"):
            errors.append(json.dumps(msg.get("content"))[:200])
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block["text"])
    final = texts[-1] if texts else ""
    dst = OUT / cond / f"draw-{draw}"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "output.txt").write_text(final + "\n")
    (dst / "session-id.txt").write_text(src.stem + "\n")
    shutil.copy2(src, dst / "session.jsonl")
    status = "ERROR " + errors[0] if errors else ("EMPTY" if not final else "ok")
    rows.append((cond, draw, model, f"{status}  ({len(final)} chars)"))

for cond, draw, model, status in rows:
    print(f"{cond:<14} draw-{draw}  {model}  {status}")
print(f"\n{len(rows)} sessions recovered")
