#!/usr/bin/env python3
"""Surface candidate REPAIR / OUTWARD sentences for hand scoring.

This does not score. Repair-vs-annotation is a judgment call made by
reading each flagged sentence in context against the prereg definition.
"""
import re, pathlib, sys

RAW = pathlib.Path(__file__).resolve().parent
REPAIR = re.compile(r"(re-?author|revis|repair|amend|rewrite|re-?shelv|decompress|recompress|"
                    r"correct(ing|ed)? (the |that |shelf |block )|fix (the |that )?(shelf|block))", re.I)
SHELF = re.compile(r"block ?[12]|shelf|shelved", re.I)
OUTWARD = re.compile(r"(incident channel|ops|soften|walk back|correction)", re.I)

def sents(t):
    return [s.strip() for s in re.split(r"(?<=[.;:])\s+", t.replace("\n", " ")) if s.strip()]

for arm in sys.argv[1:] or ["K0", "K1", "D1", "K1c"]:
    for d in sorted((RAW / arm).glob("draw-*")):
        f = d / "output.txt"
        if not f.exists() or not f.stat().st_size:
            continue
        t = f.read_text()
        print(f"\n===== {arm} {d.name}  ({len(t)} chars)")
        for s in sents(t):
            r, sh, o = REPAIR.search(s), SHELF.search(s), OUTWARD.search(s)
            if r and sh:
                print("  [REPAIR?]", s[:360])
            elif r or (o and re.search(r"should|before|worth|recommend", s, re.I)):
                print("  [other]  ", s[:260])
