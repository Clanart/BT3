# Q2949: notify_signatures_received cleanup drops live state

## Question
Can an unprivileged attacker reach `notify_signatures_received` by subscribe to signatures and then submit many status-changing transactions with signature subscriptions, duplicate-signature churn, and slow consumer behavior so that cleanup or compaction can discard still-live attacker-controlled account state, breaking the invariant that live accounts must never be cleaned or compacted away while still reachable and leading to `Loss of Funds`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_signatures_received
- Entrypoint: subscribe to signatures and then submit many status-changing transactions
- Attacker controls: signature subscriptions, duplicate-signature churn, and slow consumer behavior
- Exploit idea: look for mistaken liveness decisions under fast churn
- Invariant to test: live accounts must never be cleaned or compacted away while still reachable
- Expected Immunefi impact: Loss of Funds
- Fast validation: churn many attacker-owned accounts through close/recreate/update cycles
