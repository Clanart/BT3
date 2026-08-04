# Q649: blockSubscribe slow-path lock contention

## Question
Can an unprivileged attacker use `blockSubscribe` within the single-client low-rate model and choose subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client such that `block_subscribe` triggers a path where this method may hold a shared lock or guard long enough to degrade other request classes, violating the invariant that one heavy request must not monopolize shared locks at low rate and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::block_subscribe
- Entrypoint: WebSocket `blockSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: treat lock hold time as the exploit surface
- Invariant to test: one heavy request must not monopolize shared locks at low rate
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: trace lock hold times during boundary requests
