# Q2954: notify_signatures_received remove-unrooted state loss

## Question
Can an unprivileged attacker reach `notify_signatures_received` by subscribe to signatures and then submit many status-changing transactions with signature subscriptions, duplicate-signature churn, and slow consumer behavior so that state the runtime or RPC still needs can be removed because slot liveness assumptions are too aggressive, breaking the invariant that only truly unreachable unrooted state should be removed and leading to `Loss of Funds`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_signatures_received
- Entrypoint: subscribe to signatures and then submit many status-changing transactions
- Attacker controls: signature subscriptions, duplicate-signature churn, and slow consumer behavior
- Exploit idea: look for premature removal under churn
- Invariant to test: only truly unreachable unrooted state should be removed
- Expected Immunefi impact: Loss of Funds
- Fast validation: drive fast fork/root churn with attacker-owned accounts and verify consistency afterward
