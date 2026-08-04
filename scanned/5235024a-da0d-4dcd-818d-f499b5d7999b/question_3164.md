# Q3164: filter_signature_result watcher leak on disconnect

## Question
Can an unprivileged attacker reach `filter_signature_result` by use in-scope signature subscriptions and many status changes with signature subscription parameters and hot status churn so that disconnect/unsubscribe races leave watcher state or queued notifications behind, breaking the invariant that watcher teardown must reclaim all state promptly and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::filter_signature_result
- Entrypoint: use in-scope signature subscriptions and many status changes
- Attacker controls: signature subscription parameters and hot status churn
- Exploit idea: stress teardown paths
- Invariant to test: watcher teardown must reclaim all state promptly
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: rapidly connect/disconnect and compare live watcher counts before and after
