# Q2689: remove watcher leak on disconnect

## Question
Can an unprivileged attacker reach `remove` by make low-rate in-scope rpc reads during account churn with read-after-delete and recreate patterns against the same accounts so that disconnect/unsubscribe races leave watcher state or queued notifications behind, breaking the invariant that watcher teardown must reclaim all state promptly and leading to `RPC DoS/Crash`?

## Target
- File/function: accounts-db/src/read_only_accounts_cache.rs::remove
- Entrypoint: make low-rate in-scope RPC reads during account churn
- Attacker controls: read-after-delete and recreate patterns against the same accounts
- Exploit idea: stress teardown paths
- Invariant to test: watcher teardown must reclaim all state promptly
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: rapidly connect/disconnect and compare live watcher counts before and after
