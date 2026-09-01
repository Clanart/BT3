# Q4473: `check_if_kickoff_malicious` and the chain view it trusts

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees influence the data `check_if_kickoff_malicious` in `core/src/states/event.rs` reads about the chain (a transaction visible only in the mempool, an unconfirmed parent, a block fetched by hash after a reorg) so a bridge decision is made from a view that no longer, or does not yet, hold?

## Target
- File/function: `core/src/states/event.rs` -> `check_if_kickoff_malicious`
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `check_if_kickoff_malicious`
- Attacker controls: mempool presence, replacement and confirmation of the attacker's transactions; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: make a bridge decision from an unconfirmed or stale view
- Invariant to test: bridge decisions are made only from confirmed data at the required depth
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: assert `check_if_kickoff_malicious` ignores mempool-only and orphaned data
