# Q724: signatureSubscribe result-size amplification

## Question
Can an unprivileged attacker enter through `signatureSubscribe` and supply subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client so that `signature_subscribe` hits a path where one logical event becomes an oversized notification object graph because of attacker-selected detail levels, breaking the invariant that notification size should stay bounded and predictable for each event type and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::signature_subscribe
- Entrypoint: WebSocket `signatureSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: use supported detail flags to maximize notification weight
- Invariant to test: notification size should stay bounded and predictable for each event type
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: replay the same event stream across detail levels and compare heap and wire size
