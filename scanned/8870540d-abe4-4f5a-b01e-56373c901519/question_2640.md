# Q2640: read_only_accounts_cache.load hot-stream starvation

## Question
Can an unprivileged attacker reach `load` by make low-rate in-scope rpc reads that repeatedly fetch recently changed accounts with rapid read-after-write patterns against the same accounts so that one hot account/program/signature stream monopolizes work and starves other subscribers, breaking the invariant that one subscription stream must not starve unrelated streams and leading to `RPC DoS/Crash`?

## Target
- File/function: accounts-db/src/read_only_accounts_cache.rs::load
- Entrypoint: make low-rate in-scope RPC reads that repeatedly fetch recently changed accounts
- Attacker controls: rapid read-after-write patterns against the same accounts
- Exploit idea: measure cross-subscriber fairness
- Invariant to test: one subscription stream must not starve unrelated streams
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: pair a hot stream with a cheap control subscription
