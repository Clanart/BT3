# Q3116: filter_block_result_txs result-cloning chain

## Question
Can an unprivileged attacker reach `filter_block_result_txs` by use in-scope block subscriptions and hot block contents with block subscription filters, detail levels, and hot block streams so that large notification objects are cloned more than necessary, breaking the invariant that notification emission should avoid redundant cloning of large payloads and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::filter_block_result_txs
- Entrypoint: use in-scope block subscriptions and hot block contents
- Attacker controls: block subscription filters, detail levels, and hot block streams
- Exploit idea: look for repeated clones of the same large object
- Invariant to test: notification emission should avoid redundant cloning of large payloads
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: profile allocations and clone counts while delivering large notifications
