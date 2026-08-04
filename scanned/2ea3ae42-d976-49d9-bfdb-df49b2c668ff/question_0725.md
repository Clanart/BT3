# Q725: signatureSubscribe watcher starvation

## Question
Can an unprivileged attacker enter through `signatureSubscribe` and supply subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client so that `signature_subscribe` hits a path where one hot subscription can block or delay unrelated watchers on the same service, breaking the invariant that one watcher must not starve unrelated watchers and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::signature_subscribe
- Entrypoint: WebSocket `signatureSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: look for shared notifier or lock contention between subscription classes
- Invariant to test: one watcher must not starve unrelated watchers
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: combine one hottest subscription with a cheap control subscription
