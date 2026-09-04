# Q4094: from_seed: fee debited differs from get_tx_fee

## Question
Can an unprivileged attacker reach `from_seed` (in `stacks-common/src/util/secp256k1/native.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that the charged fee diverges from the declared fee on an abort path, breaking the invariant that fee debited == get_tx_fee — leading to fee accounting error?

## Target
- File/function: `stacks-common/src/util/secp256k1/native.rs` -> `from_seed`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: the charged fee diverges from the declared fee on an abort path
- Invariant to test: fee debited == get_tx_fee
- Expected Immunefi impact: High - fee accounting error
- Fast validation: test an aborting tx asserting fee
