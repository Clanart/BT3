# Q5922: clear_before_coinbase_height: chain_id compared against the wrong network constant

## Question
Can an unprivileged attacker reach `clear_before_coinbase_height` (in `stackslib/src/core/mempool.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that a crafted chain_id passes one check and fails another, breaking the invariant that the chain a tx is valid on == exactly the configured chain — leading to cross-network replay?

## Target
- File/function: `stackslib/src/core/mempool.rs` -> `clear_before_coinbase_height`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: a crafted chain_id passes one check and fails another
- Invariant to test: the chain a tx is valid on == exactly the configured chain
- Expected Immunefi impact: Critical - cross-network replay
- Fast validation: test a mismatched chain_id
