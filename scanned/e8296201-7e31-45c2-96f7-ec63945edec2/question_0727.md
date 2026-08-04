# Q727: signatureSubscribe single-client crash path

## Question
Can an unprivileged attacker enter through `signatureSubscribe` and supply subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client so that `signature_subscribe` hits a path where validly encoded subscription params may still reach a panic, assert, or fatal allocation path, breaking the invariant that one client subscription request must not crash the service and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::signature_subscribe
- Entrypoint: WebSocket `signatureSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: treat the subscription API as a crash surface as well as a backlog surface
- Invariant to test: one client subscription request must not crash the service
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: fuzz only valid subscription parameter combinations
