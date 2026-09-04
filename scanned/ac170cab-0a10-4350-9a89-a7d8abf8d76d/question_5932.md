# Q5932: clear_before_coinbase_height: anchor_mode/sponsor toggle not covered by the hash

## Question
Can an unprivileged attacker reach `clear_before_coinbase_height` (in `stackslib/src/core/mempool.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that toggling a mode changes execution but not the digest, breaking the invariant that every executed mode == a mode the signature covered — leading to unauthorised mode change?

## Target
- File/function: `stackslib/src/core/mempool.rs` -> `clear_before_coinbase_height`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: toggling a mode changes execution but not the digest
- Invariant to test: every executed mode == a mode the signature covered
- Expected Immunefi impact: Critical - unauthorised mode change
- Fast validation: test toggling a mode post-signing
