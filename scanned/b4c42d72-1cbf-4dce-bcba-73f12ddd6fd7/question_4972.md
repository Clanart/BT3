# Q4972: secp256k1_recover: anchor_mode/sponsor toggle not covered by the hash

## Question
Can an unprivileged attacker reach `secp256k1_recover` (in `stacks-common/src/util/secp256k1/wasm.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that toggling a mode changes execution but not the digest, breaking the invariant that every executed mode == a mode the signature covered — leading to unauthorised mode change?

## Target
- File/function: `stacks-common/src/util/secp256k1/wasm.rs` -> `secp256k1_recover`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: toggling a mode changes execution but not the digest
- Invariant to test: every executed mode == a mode the signature covered
- Expected Immunefi impact: Critical - unauthorised mode change
- Fast validation: test toggling a mode post-signing
