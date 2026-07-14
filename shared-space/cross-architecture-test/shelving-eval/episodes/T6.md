# T6 — Work history

USER: Checkout latency spiked yesterday 14:00–14:40 UTC. What happened?

ASSISTANT: Explained. The spike matches a burst of cache evictions: a
deploy at 13:58 (`release-2214`) shipped a config change that halved the
`product_details` cache TTL from 600s to 300s — a deliberate change from
the pricing-freshness ticket (PRICE-990). The eviction burst refilled
over ~40 minutes and latency returned to baseline by 14:40. This is
expected warm-up behavior for a TTL change of that size; no action
needed, though I've noted in PRICE-990 that future TTL reductions should
ship with a cache pre-warmer.

While pulling the latency dashboards I also noticed something unrelated:
the p99 for `POST /cart/apply-coupon` has been drifting upward ~3% per
week for six weeks — slow, steady, unexplained, and it doesn't correlate
with any deploy in that window. I didn't investigate; it's outside the
spike question.

USER: OK — the spike explanation makes sense. Write this up and shelve it.
