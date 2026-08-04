# Q700: programSubscribe subscription leak

## Question
Can an unprivileged attacker enter through `programSubscribe` and supply subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client so that `program_subscribe` hits a path where subscribe/unsubscribe or disconnect timing can leave watcher state behind indefinitely, breaking the invariant that dead subscriptions must be reclaimed promptly and completely and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::program_subscribe
- Entrypoint: WebSocket `programSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: exercise subscription churn and cleanup paths rather than only delivery
- Invariant to test: dead subscriptions must be reclaimed promptly and completely
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: repeatedly subscribe, disconnect, and reconnect while tracking live watcher counts
