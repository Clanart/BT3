# Q2739: accounts_cache.load watcher leak on disconnect

## Question
Can an unprivileged attacker reach `load` by submit transactions plus immediate reads for recently changed accounts with same-pubkey churn plus immediate readback so that disconnect/unsubscribe races leave watcher state or queued notifications behind, breaking the invariant that watcher teardown must reclaim all state promptly and leading to `RPC DoS/Crash`?

## Target
- File/function: accounts-db/src/accounts_cache.rs::load
- Entrypoint: submit transactions plus immediate reads for recently changed accounts
- Attacker controls: same-pubkey churn plus immediate readback
- Exploit idea: stress teardown paths
- Invariant to test: watcher teardown must reclaim all state promptly
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: rapidly connect/disconnect and compare live watcher counts before and after
