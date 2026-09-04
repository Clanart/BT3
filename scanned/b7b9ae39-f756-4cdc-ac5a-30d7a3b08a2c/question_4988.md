# Q4988: secp256k1_verify: an asset moved by a sub-call escapes Deny-mode post-conditions

## Question
Can an unprivileged attacker reach `secp256k1_verify` (in `stacks-common/src/util/secp256k1/wasm.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that a nested contract-call moves an asset not attributed to the named principal, breaking the invariant that every committed movement == a movement its post-conditions permit — leading to theft escaping post-conditions?

## Target
- File/function: `stacks-common/src/util/secp256k1/wasm.rs` -> `secp256k1_verify`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: a nested contract-call moves an asset not attributed to the named principal
- Invariant to test: every committed movement == a movement its post-conditions permit
- Expected Immunefi impact: Critical - theft escaping post-conditions
- Fast validation: test a nested transfer under Deny mode
