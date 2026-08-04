# Q2539: add_root_and_flush_write_cache watcher leak on disconnect

## Question
Can an unprivileged attacker reach `add_root_and_flush_write_cache` by submit transactions that write many accounts near root transitions with many-account write bursts plus immediate root/read churn so that disconnect/unsubscribe races leave watcher state or queued notifications behind, breaking the invariant that watcher teardown must reclaim all state promptly and leading to `RPC DoS/Crash`?

## Target
- File/function: accounts-db/src/accounts_db.rs::add_root_and_flush_write_cache
- Entrypoint: submit transactions that write many accounts near root transitions
- Attacker controls: many-account write bursts plus immediate root/read churn
- Exploit idea: stress teardown paths
- Invariant to test: watcher teardown must reclaim all state promptly
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: rapidly connect/disconnect and compare live watcher counts before and after
