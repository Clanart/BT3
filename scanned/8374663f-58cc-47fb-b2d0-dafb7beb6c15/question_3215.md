# Q3215: filter_logs_results hot-stream starvation

## Question
Can an unprivileged attacker reach `filter_logs_results` by use in-scope logs subscriptions with legal filters with logs filters, encodings, and log-heavy transactions so that one hot account/program/signature stream monopolizes work and starves other subscribers, breaking the invariant that one subscription stream must not starve unrelated streams and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::filter_logs_results
- Entrypoint: use in-scope logs subscriptions with legal filters
- Attacker controls: logs filters, encodings, and log-heavy transactions
- Exploit idea: measure cross-subscriber fairness
- Invariant to test: one subscription stream must not starve unrelated streams
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: pair a hot stream with a cheap control subscription
