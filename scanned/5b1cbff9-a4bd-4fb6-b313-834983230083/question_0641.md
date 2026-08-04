# Q641: blockSubscribe duplicate delivery state

## Question
Can an unprivileged attacker enter through `blockSubscribe` and supply subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client so that `block_subscribe` hits a path where retries, reconnects, or slot churn may grow dedup/tracking state without bound for one client, breaking the invariant that per-client dedup or replay tracking must remain bounded and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::block_subscribe
- Entrypoint: WebSocket `blockSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: target deduplication or replay-tracking maps rather than raw delivery throughput
- Invariant to test: per-client dedup or replay tracking must remain bounded
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: oscillate reconnects around hot events and track tracker-map size
