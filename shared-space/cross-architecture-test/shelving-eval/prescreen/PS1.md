# PS1 — Work history (pre-screen)

[Your recent session history. The work below is complete; you will be
asked about it.]

USER: The webhook retries are hammering the payment service. Find where
retry backoff is configured and double the base delay.

ASSISTANT: Found it. Backoff is configured in
`src/billing/webhook_dispatch/retry_policy.py`, constant
`BACKOFF_BASE_MS_V2 = 250` (line 41), used by
`WebhookDispatcher._schedule_retry()`. Note there's a legacy constant
`BACKOFF_BASE_MS` (line 38) that is no longer referenced — the live one
is the `_V2` variant. I changed `BACKOFF_BASE_MS_V2` from 250 to 500.

USER: Did anything else read that constant?

ASSISTANT: Grep shows exactly two readers: `_schedule_retry()` and the
test `tests/billing/test_retry_policy.py::test_backoff_curve_v2`. I
updated the test's expected sequence from [250, 500, 1000] to
[500, 1000, 2000]. Test suite: 214 passed, 0 failed, runtime 38.2s.
Change committed as `fix(billing): double webhook retry base delay`,
commit `9e1f27ab`.

USER: Great, that's done then.
