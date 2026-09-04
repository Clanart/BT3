# Q0738: check_transaction_postconditions: trailing bytes accepted after a transaction

## Question
Can an unprivileged attacker reach `check_transaction_postconditions` (in `crates/stacks-transactions/src/lib.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that the codec ignores extra bytes past the payload, breaking the invariant that bytes consumed == bytes present, exactly — leading to transaction ambiguity?

## Target
- File/function: `crates/stacks-transactions/src/lib.rs` -> `check_transaction_postconditions`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: the codec ignores extra bytes past the payload
- Invariant to test: bytes consumed == bytes present, exactly
- Expected Immunefi impact: High - transaction ambiguity
- Fast validation: test a tx with trailing bytes
