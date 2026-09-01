# Q4438: `collateral_check` and the chain view it trusts

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees influence the data `collateral_check` in `core/src/extended_bitcoin_rpc.rs` reads about the chain (a transaction visible only in the mempool, an unconfirmed parent, a block fetched by hash after a reorg) so a bridge decision is made from a view that no longer, or does not yet, hold?

## Target
- File/function: `core/src/extended_bitcoin_rpc.rs` -> `collateral_check` (Extended RPC interface communicates with the Bitcoin node. It features some)
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `collateral_check`
- Attacker controls: mempool presence, replacement and confirmation of the attacker's transactions; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: make a bridge decision from an unconfirmed or stale view
- Invariant to test: bridge decisions are made only from confirmed data at the required depth
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: assert `collateral_check` ignores mempool-only and orphaned data
