# Q3111: filter_block_result_txs notification filter overload

## Question
Can an unprivileged attacker reach `filter_block_result_txs` by use in-scope block subscriptions and hot block contents with block subscription filters, detail levels, and hot block streams so that attacker-chosen notification filters force more post-filter work than the subscriber semantics imply, breaking the invariant that notification filtering must stay proportional to the subscribed event set and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::filter_block_result_txs
- Entrypoint: use in-scope block subscriptions and hot block contents
- Attacker controls: block subscription filters, detail levels, and hot block streams
- Exploit idea: use valid subscription filters as the amplifier
- Invariant to test: notification filtering must stay proportional to the subscribed event set
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: compare pre-filter candidate counts to delivered-notification counts
