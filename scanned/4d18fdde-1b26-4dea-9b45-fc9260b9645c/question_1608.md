# Q1608: from_parent_unsigned: trailing bytes accepted after a transaction

## Question
Can an unprivileged attacker reach `from_parent_unsigned` (in `stacks-codec/src/transaction.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that the codec ignores extra bytes past the payload, breaking the invariant that bytes consumed == bytes present, exactly — leading to transaction ambiguity?

## Target
- File/function: `stacks-codec/src/transaction.rs` -> `from_parent_unsigned`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: the codec ignores extra bytes past the payload
- Invariant to test: bytes consumed == bytes present, exactly
- Expected Immunefi impact: High - transaction ambiguity
- Fast validation: test a tx with trailing bytes
