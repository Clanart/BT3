# Q635: blockSubscribe filter amplification

## Question
Can an unprivileged attacker enter through `blockSubscribe` and supply subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client so that `block_subscribe` hits a path where attacker-chosen filters or encodings make each notification materially more expensive than the underlying event, breaking the invariant that per-notification formatting must remain bounded for one subscriber and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::block_subscribe
- Entrypoint: WebSocket `blockSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: target the notification-formatting stage rather than the triggering event itself
- Invariant to test: per-notification formatting must remain bounded for one subscriber
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: drive the hottest legal notification source and compare formatting cost
