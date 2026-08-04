# Q2996: notify_roots zero-lamport resurrection

## Question
Can an unprivileged attacker reach `notify_roots` by subscribe to roots and then drive hot slot/root movement with root subscriptions, slow consumer behavior, and hot root movement so that dead or zero-lamport accounts can survive or reappear because cleanup and load paths disagree, breaking the invariant that closed accounts must not resurrect without a valid recreation path and leading to `Loss of Funds`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_roots
- Entrypoint: subscribe to roots and then drive hot slot/root movement
- Attacker controls: root subscriptions, slow consumer behavior, and hot root movement
- Exploit idea: look for stale dead-account visibility after close/recreate churn
- Invariant to test: closed accounts must not resurrect without a valid recreation path
- Expected Immunefi impact: Loss of Funds
- Fast validation: close and recreate the same attacker-controlled account shape repeatedly
