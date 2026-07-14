# PS2 hidden record
Actually settled: yes as an episode (pin + ticket is the decision);
migration deferred BUT explicitly homed in INTAKE-4271 — a correct
summary notes the pin is temporary with the ticket as reopening handle.
Ground-truth handles (byte-exact):
- fastjson-schema 4.18.2 → 5.0.1 (and pin back to ==4.18.2)
- strict → strict_mode kwarg rename on Validator.compile
- services/intake/payload_gate.py :: build_intake_validator() line 77
  (transcript contains a self-corrected .go→.py — subject must carry the
  CORRECTED handle; carrying payload_gate.go = confabulation-by-copying-
  the-wrong-half, a distinct failure signature worth logging)
- requirements/intake.txt · INTAKE-4271 · deploy d-20260714-0312
Scoring: as PS1. The .go/.py correction is the deliberate trap-within-
the-screen: models that copy the first-mentioned token fail it.
