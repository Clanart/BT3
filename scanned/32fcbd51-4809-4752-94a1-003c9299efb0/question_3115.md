# Q3115: filter_block_result_txs hot-stream starvation

## Question
Can an unprivileged attacker reach `filter_block_result_txs` by use in-scope block subscriptions and hot block contents with block subscription filters, detail levels, and hot block streams so that one hot account/program/signature stream monopolizes work and starves other subscribers, breaking the invariant that one subscription stream must not starve unrelated streams and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::filter_block_result_txs
- Entrypoint: use in-scope block subscriptions and hot block contents
- Attacker controls: block subscription filters, detail levels, and hot block streams
- Exploit idea: measure cross-subscriber fairness
- Invariant to test: one subscription stream must not starve unrelated streams
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: pair a hot stream with a cheap control subscription
