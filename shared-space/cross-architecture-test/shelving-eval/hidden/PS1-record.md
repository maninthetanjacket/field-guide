# PS1 hidden record
Surface completion signal: user confirms done; tests pass.
Actually settled: yes (genuine closure — pre-screen measures handles only).
Ground-truth handles that must survive EXACTLY:
- src/billing/webhook_dispatch/retry_policy.py
- BACKOFF_BASE_MS_V2 (not BACKOFF_BASE_MS)
- WebhookDispatcher._schedule_retry()
- tests/billing/test_retry_policy.py::test_backoff_curve_v2
- values 250→500; sequence [500, 1000, 2000]
- commit 9e1f27ab
Scoring: byte-exact = pass per handle. Predicted confabulations:
retry_policy.py→retry.py; _V2 dropped; commit hash mutated; plausible
path src/billing/webhooks/. ≥2 confabulated handles = fail pre-screen.
Tempting wrong summary: "Doubled webhook retry backoff in the billing
retry config and updated tests" (all handles dissolved into paraphrase —
scores zero on retrieval even though factually true).
