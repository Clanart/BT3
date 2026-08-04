# Q626: slotSubscribe heap retention after send

## Question
Can an unprivileged attacker use `slotSubscribe` within the single-client low-rate model and choose subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client such that `slot_subscribe` triggers a path where large intermediate objects may survive longer than response emission or websocket flush, violating the invariant that transient request state should be released promptly after response emission and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::slot_subscribe
- Entrypoint: WebSocket `slotSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: treat allocator lifetime as the bug class rather than raw allocation size
- Invariant to test: transient request state should be released promptly after response emission
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: record heap over time during repeated heavy calls
