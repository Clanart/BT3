# Q4849: new: contract-call argument Value deserializes past its declared type

## Question
Can an unprivileged attacker reach `new` (in `stacks-common/src/util/secp256k1/wasm.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that a payload arg Value violates its type and the callee trusts it, breaking the invariant that every arg Value == a Value of its declared type — leading to type confusion in the called contract?

## Target
- File/function: `stacks-common/src/util/secp256k1/wasm.rs` -> `new`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: a payload arg Value violates its type and the callee trusts it
- Invariant to test: every arg Value == a Value of its declared type
- Expected Immunefi impact: Critical - type confusion in the called contract
- Fast validation: test an over-typed argument
