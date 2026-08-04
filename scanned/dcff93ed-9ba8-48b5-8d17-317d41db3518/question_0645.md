# Q645: blockSubscribe cold-cache amplification

## Question
Can an unprivileged attacker use `blockSubscribe` within the single-client low-rate model and choose subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client such that `block_subscribe` triggers a path where a legal cold-start request shape costs materially more than the warm path and is attacker-repeatable, violating the invariant that cold-path cost should still be bounded for a single client and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::block_subscribe
- Entrypoint: WebSocket `blockSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: force cold reads, cache misses, or empty caches through one public method
- Invariant to test: cold-path cost should still be bounded for a single client
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: alternate request parameters to defeat warm caches
