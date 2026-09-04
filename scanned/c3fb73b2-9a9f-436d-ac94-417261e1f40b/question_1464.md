# Q1464: from_order_independent_p2wsh: multisig signature threaded over the wrong running hash

## Question
Can an unprivileged attacker reach `from_order_independent_p2wsh` (in `stacks-codec/src/transaction.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that `next_signature` advances the hash so a signature covers a different sighash than expected, breaking the invariant that each signature verified == over the correct running sighash — leading to multisig auth bypass?

## Target
- File/function: `stacks-codec/src/transaction.rs` -> `from_order_independent_p2wsh`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: `next_signature` advances the hash so a signature covers a different sighash than expected
- Invariant to test: each signature verified == over the correct running sighash
- Expected Immunefi impact: Critical - multisig auth bypass
- Fast validation: test a mis-threaded sequential multisig
