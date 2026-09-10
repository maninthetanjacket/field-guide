#!/usr/bin/env python3
"""Byte-exact handle scorer for the shelving-eval pre-screen."""
import sys

RUNS = "/tmp/shelving-eval-run/prescreen-qwen38"

PS1_HANDLES = [
    ("path",        "src/billing/webhook_dispatch/retry_policy.py"),
    ("live const",  "BACKOFF_BASE_MS_V2"),
    ("reader fn",   "WebhookDispatcher._schedule_retry()"),
    ("test id",     "tests/billing/test_retry_policy.py::test_backoff_curve_v2"),
    ("old value",   "250"),
    ("new value",   "500"),
    ("commit",      "9e1f27ab"),
]
PS1_BONUS = [
    ("legacy twin", "BACKOFF_BASE_MS"),
    ("new seq",     "[500, 1000, 2000]"),
    ("old seq",     "[250, 500, 1000]"),
    ("suite",       "214 passed"),
    ("line 41",     "line 41"),
    ("line 38",     "line 38"),
]
PS1_CONFAB = [
    "retry.py", "src/billing/webhooks/", "BACKOFF_MS_BASE_V2",
    "webhook_dispatcher.py", "_schedule_retries",
]

PS2_HANDLES = [
    ("pkg",         "fastjson-schema"),
    ("old ver",     "4.18.2"),
    ("new ver",     "5.0.1"),
    ("pin",         "==4.18.2"),
    ("new kwarg",   "strict_mode"),
    ("api",         "Validator.compile"),
    ("path (.py)",  "services/intake/payload_gate.py"),
    ("fn",          "build_intake_validator()"),
]
PS2_BONUS = [
    ("reqs file",   "requirements/intake.txt"),
    ("ticket",      "INTAKE-4271"),
    ("deploy id",   "d-20260714-0312"),
    ("line 77",     "line 77"),
]
PS2_CONFAB = [
    "payload_gate.go", "services/intake/intake_gate.py", "INTAKE-4217",
    "fastjson_schema", "4.18.1",
]


def load(ep):
    with open(f"{RUNS}/{ep}/output.txt") as f:
        raw = f.read()
    # strip CLI banner lines that are not model output
    return "\n".join(
        l for l in raw.splitlines()
        if not l.startswith("⚠") and not l.startswith("[claude-code:")
    )


def score(ep, core, bonus, confab):
    txt = load(ep)
    print(f"===== {ep} =====")
    hits = 0
    for label, h in core:
        ok = h in txt
        hits += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:<12} {h!r}")
    print(f"  CORE: {hits}/{len(core)}")
    bh = [(l, h) for l, h in bonus if h in txt]
    print(f"  BONUS retained ({len(bh)}/{len(bonus)}): "
          + ", ".join(l for l, _ in bh))
    found = [c for c in confab if c in txt]
    print(f"  CONFABULATIONS: {found if found else 'none'}")
    return hits, len(core), found


if __name__ == "__main__":
    r1 = score("PS1", PS1_HANDLES, PS1_BONUS, PS1_CONFAB)
    print()
    r2 = score("PS2", PS2_HANDLES, PS2_BONUS, PS2_CONFAB)

    # PS2 .go trap: the .py handle must be present; a bare payload_gate.go
    # used AS the handle is the failure. Mentioning the correction is fine.
    txt2 = load("PS2")
    print("\n===== PS2 .go/.py trap =====")
    print(f"  carries corrected .py handle : {'services/intake/payload_gate.py' in txt2}")
    print(f"  mentions .go at all          : {'.go' in txt2}")
    print(f"  names it AS a correction     : "
          f"{any(w in txt2.lower() for w in ['corrected', 'correction', 'misstat', 'initially'])}")

    total_confab = len(r1[2]) + len(r2[2])
    verdict = "QUALIFIED" if (r1[0] == r1[1] and r2[0] == r2[1]
                              and total_confab < 2) else "REVIEW"
    print(f"\nVERDICT: {verdict}  "
          f"(core {r1[0]}/{r1[1]} + {r2[0]}/{r2[1]}, confabulations {total_confab})")
