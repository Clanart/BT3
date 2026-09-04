# Q2841: pop_auth_field: sighash cleared-auth restore leaves a field blanked

## Question
Can an unprivileged attacker reach `pop_auth_field` (in `stacks-codec/src/transaction.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that the clear/restore in `next_signature` drops a field permanently, breaking the invariant that the tx hashed == the tx executed after restore — leading to malleation?

## Target
- File/function: `stacks-codec/src/transaction.rs` -> `pop_auth_field`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: the clear/restore in `next_signature` drops a field permanently
- Invariant to test: the tx hashed == the tx executed after restore
- Expected Immunefi impact: Critical - malleation
- Fast validation: test the clear/restore round-trip
