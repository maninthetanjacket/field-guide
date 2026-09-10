#!/usr/bin/env python3
"""Audit subject sessions for exposure to the user-level shelving skill.

Three exposure levels per session:
  INVOKED  - subject called the Skill tool (full SKILL.md entered context)
  LISTED   - skill name/description present in the session's context
             (the skill listing reminder), but never invoked
  none     - no trace of the skill
Also records any tool_use calls, for the MCP/tool picture.
"""
import json, pathlib, re, sys

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
GROUPS = {
    "prescreen": "-tmp-shelving-eval-run-prescreen-qwen38-",
    "A0":        "-tmp-shelving-eval-run-A0-qwen38-",
    "variance":  "-tmp-shelving-eval-run-variance-qwen38-",
}

def audit(jsonl):
    invoked, listed, tools = False, False, []
    for line in jsonl.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        blob = json.dumps(e)
        if "skills/shelving" in blob or re.search(r'"shelving:|\bshelving: Deliberate', blob):
            listed = True
        msg = e.get("message") or {}
        for b in (msg.get("content") or []) if isinstance(msg.get("content"), list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                name = b.get("name")
                tools.append(name)
                if name == "Skill":
                    invoked = True
    return invoked, listed, tools

for group, prefix in GROUPS.items():
    dirs = sorted(PROJECTS.glob(prefix + "*"))
    print(f"===== {group} ({len(dirs)} sessions)")
    for d in dirs:
        for j in sorted(d.glob("*.jsonl")):
            inv, lst, tools = audit(j)
            level = "INVOKED" if inv else ("LISTED" if lst else "none")
            print(f"  {d.name[len(prefix):]:<24} skill={level:<8} tools={tools}")
