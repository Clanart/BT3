# Q2636: read_only_accounts_cache.load notification filter overload

## Question
Can an unprivileged attacker reach `load` by make low-rate in-scope rpc reads that repeatedly fetch recently changed accounts with rapid read-after-write patterns against the same accounts so that attacker-chosen notification filters force more post-filter work than the subscriber semantics imply, breaking the invariant that notification filtering must stay proportional to the subscribed event set and leading to `RPC DoS/Crash`?

## Target
- File/function: accounts-db/src/read_only_accounts_cache.rs::load
- Entrypoint: make low-rate in-scope RPC reads that repeatedly fetch recently changed accounts
- Attacker controls: rapid read-after-write patterns against the same accounts
- Exploit idea: use valid subscription filters as the amplifier
- Invariant to test: notification filtering must stay proportional to the subscribed event set
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: compare pre-filter candidate counts to delivered-notification counts
