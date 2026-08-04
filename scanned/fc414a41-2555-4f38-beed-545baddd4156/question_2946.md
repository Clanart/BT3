# Q2946: notify_signatures_received zero-lamport resurrection

## Question
Can an unprivileged attacker reach `notify_signatures_received` by subscribe to signatures and then submit many status-changing transactions with signature subscriptions, duplicate-signature churn, and slow consumer behavior so that dead or zero-lamport accounts can survive or reappear because cleanup and load paths disagree, breaking the invariant that closed accounts must not resurrect without a valid recreation path and leading to `Loss of Funds`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_signatures_received
- Entrypoint: subscribe to signatures and then submit many status-changing transactions
- Attacker controls: signature subscriptions, duplicate-signature churn, and slow consumer behavior
- Exploit idea: look for stale dead-account visibility after close/recreate churn
- Invariant to test: closed accounts must not resurrect without a valid recreation path
- Expected Immunefi impact: Loss of Funds
- Fast validation: close and recreate the same attacker-controlled account shape repeatedly
