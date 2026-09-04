# Q3751: was_sent: a field acted on is outside the signing hash

## Question
Can an unprivileged attacker reach `was_sent` (in `stacks-codec/src/transaction.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that `next_signature`/`verify_origin` rebuild a sighash that omits a field the node executes, breaking the invariant that the transaction the recovered key signed == the transaction executed and charged — leading to transaction forgery / malleation?

## Target
- File/function: `stacks-codec/src/transaction.rs` -> `was_sent`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: `next_signature`/`verify_origin` rebuild a sighash that omits a field the node executes
- Invariant to test: the transaction the recovered key signed == the transaction executed and charged
- Expected Immunefi impact: Critical - transaction forgery / malleation
- Fast validation: stacks-codec test mutating the field and asserting the recovered signer is unchanged
