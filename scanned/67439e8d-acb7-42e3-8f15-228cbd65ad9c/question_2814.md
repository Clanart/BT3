# Q2814: roots_to_flush watcher leak on disconnect

## Question
Can an unprivileged attacker reach `roots_to_flush` by submit transactions that keep many roots dirty while reading hot accounts with heavy write churn across nearby roots plus immediate reads so that disconnect/unsubscribe races leave watcher state or queued notifications behind, breaking the invariant that watcher teardown must reclaim all state promptly and leading to `RPC DoS/Crash`?

## Target
- File/function: accounts-db/src/accounts_cache.rs::roots_to_flush
- Entrypoint: submit transactions that keep many roots dirty while reading hot accounts
- Attacker controls: heavy write churn across nearby roots plus immediate reads
- Exploit idea: stress teardown paths
- Invariant to test: watcher teardown must reclaim all state promptly
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: rapidly connect/disconnect and compare live watcher counts before and after
