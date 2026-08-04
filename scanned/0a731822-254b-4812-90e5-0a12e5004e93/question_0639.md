# Q639: blockSubscribe result-size amplification

## Question
Can an unprivileged attacker enter through `blockSubscribe` and supply subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client so that `block_subscribe` hits a path where one logical event becomes an oversized notification object graph because of attacker-selected detail levels, breaking the invariant that notification size should stay bounded and predictable for each event type and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::block_subscribe
- Entrypoint: WebSocket `blockSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: use supported detail flags to maximize notification weight
- Invariant to test: notification size should stay bounded and predictable for each event type
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: replay the same event stream across detail levels and compare heap and wire size
