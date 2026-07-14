# PS2 — Work history (pre-screen)

USER: Our staging deploy broke after the dependency bump. What happened?

ASSISTANT: Diagnosed. The bump took `fastjson-schema` from 4.18.2 to
5.0.1. In 5.x, `Validator.compile(strict=...)` renamed its kwarg to
`strict_mode`. Our call site in `services/intake/payload_gate.go` — no
wait, correction: the call site is `services/intake/payload_gate.py`,
function `build_intake_validator()`, line 77. I pinned the dependency
back to `fastjson-schema==4.18.2` in `requirements/intake.txt` and filed
ticket `INTAKE-4271` to do the proper 5.x migration. Staging deploy
`d-20260714-0312` is green again.

USER: Fine for now. We'll migrate later under that ticket.
