# Q2683: remove read-only cache incoherence

## Question
Can an unprivileged attacker reach `remove` by make low-rate in-scope rpc reads during account churn with read-after-delete and recreate patterns against the same accounts so that read-only caching can return a version that writable/runtime paths would reject as stale, breaking the invariant that read-only caches must stay coherent with runtime-visible state and leading to `Consensus/Safety Violations`?

## Target
- File/function: accounts-db/src/read_only_accounts_cache.rs::remove
- Entrypoint: make low-rate in-scope RPC reads during account churn
- Attacker controls: read-after-delete and recreate patterns against the same accounts
- Exploit idea: diff read-only and runtime-visible answers for the same account
- Invariant to test: read-only caches must stay coherent with runtime-visible state
- Expected Immunefi impact: Consensus/Safety Violations
- Fast validation: compare read-only cache results to direct runtime/bank reads after writes
