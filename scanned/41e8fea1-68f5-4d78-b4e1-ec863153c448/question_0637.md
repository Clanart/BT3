# Q637: blockSubscribe disconnect race

## Question
Can an unprivileged attacker enter through `blockSubscribe` and supply subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client so that `block_subscribe` hits a path where disconnect or unsubscribe races can leave in-flight notifications targeting freed or wrong subscriber state, breaking the invariant that teardown must not leave dangling or mis-routed notifications and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::block_subscribe
- Entrypoint: WebSocket `blockSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: stress subscriber teardown during hot notification flow
- Invariant to test: teardown must not leave dangling or mis-routed notifications
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: unsubscribe during bursty delivery and watch for leaks or cross-delivery
