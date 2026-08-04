# Q3054: process_notifications remove-unrooted state loss

## Question
Can an unprivileged attacker reach `process_notifications` by trigger many hot notifications from one subscription shape with subscription mix, slow consumer behavior, and hot event streams so that state the runtime or RPC still needs can be removed because slot liveness assumptions are too aggressive, breaking the invariant that only truly unreachable unrooted state should be removed and leading to `Loss of Funds`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::process_notifications
- Entrypoint: trigger many hot notifications from one subscription shape
- Attacker controls: subscription mix, slow consumer behavior, and hot event streams
- Exploit idea: look for premature removal under churn
- Invariant to test: only truly unreachable unrooted state should be removed
- Expected Immunefi impact: Loss of Funds
- Fast validation: drive fast fork/root churn with attacker-owned accounts and verify consistency afterward
