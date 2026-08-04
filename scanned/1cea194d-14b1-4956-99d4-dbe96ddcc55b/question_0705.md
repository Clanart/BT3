# Q705: programSubscribe single-client fanout DoS

## Question
Can an unprivileged attacker enter through `programSubscribe` and supply subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client so that `program_subscribe` hits a path where one client can choose a subscription shape that forces per-slot fanout work disproportionate to the value of the feed, breaking the invariant that a single subscription must not dominate notifier cpu at normal slot cadence and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::program_subscribe
- Entrypoint: WebSocket `programSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: treat one-client fanout cost as the core invariant
- Invariant to test: a single subscription must not dominate notifier CPU at normal slot cadence
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: select the heaviest in-scope subscription shape and measure notifier CPU
