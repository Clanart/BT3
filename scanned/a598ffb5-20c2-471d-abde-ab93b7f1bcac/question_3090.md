# Q3090: notify_watchers hot-stream starvation

## Question
Can an unprivileged attacker reach `notify_watchers` by trigger many hot notifications from one subscription shape with subscription mix, slow consumer behavior, and hot event streams so that one hot account/program/signature stream monopolizes work and starves other subscribers, breaking the invariant that one subscription stream must not starve unrelated streams and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_watchers
- Entrypoint: trigger many hot notifications from one subscription shape
- Attacker controls: subscription mix, slow consumer behavior, and hot event streams
- Exploit idea: measure cross-subscriber fairness
- Invariant to test: one subscription stream must not starve unrelated streams
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: pair a hot stream with a cheap control subscription
