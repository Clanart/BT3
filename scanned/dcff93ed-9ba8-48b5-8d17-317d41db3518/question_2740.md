# Q2740: accounts_cache.load hot-stream starvation

## Question
Can an unprivileged attacker reach `load` by submit transactions plus immediate reads for recently changed accounts with same-pubkey churn plus immediate readback so that one hot account/program/signature stream monopolizes work and starves other subscribers, breaking the invariant that one subscription stream must not starve unrelated streams and leading to `RPC DoS/Crash`?

## Target
- File/function: accounts-db/src/accounts_cache.rs::load
- Entrypoint: submit transactions plus immediate reads for recently changed accounts
- Attacker controls: same-pubkey churn plus immediate readback
- Exploit idea: measure cross-subscriber fairness
- Invariant to test: one subscription stream must not starve unrelated streams
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: pair a hot stream with a cheap control subscription
