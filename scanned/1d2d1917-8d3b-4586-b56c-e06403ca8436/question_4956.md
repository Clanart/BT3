# Q4956: secp256k1_recover: origin payload executes without a valid sponsor signature

## Question
Can an unprivileged attacker reach `secp256k1_recover` (in `stacks-common/src/util/secp256k1/wasm.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that the sponsored path runs the payload though the sponsor auth is absent/invalid, breaking the invariant that payload executes only if both origin and sponsor auth verify — leading to unauthorised sponsored execution?

## Target
- File/function: `stacks-common/src/util/secp256k1/wasm.rs` -> `secp256k1_recover`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: the sponsored path runs the payload though the sponsor auth is absent/invalid
- Invariant to test: payload executes only if both origin and sponsor auth verify
- Expected Immunefi impact: Critical - unauthorised sponsored execution
- Fast validation: test a sponsored tx with a bad sponsor sig
