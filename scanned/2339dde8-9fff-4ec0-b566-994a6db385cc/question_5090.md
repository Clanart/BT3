# Q5090: to_bytes: clarity version newer than epoch slips a soft check

## Question
Can an unprivileged attacker reach `to_bytes` (in `stacks-common/src/util/secp256k1/wasm.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that `SmartContract(_, Some(version))` exceeds `default_for_epoch` but is admitted, breaking the invariant that the Clarity version admitted <= the epoch max — leading to admission divergence?

## Target
- File/function: `stacks-common/src/util/secp256k1/wasm.rs` -> `to_bytes`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: `SmartContract(_, Some(version))` exceeds `default_for_epoch` but is admitted
- Invariant to test: the Clarity version admitted <= the epoch max
- Expected Immunefi impact: Critical - admission divergence
- Fast validation: test an over-version deploy
