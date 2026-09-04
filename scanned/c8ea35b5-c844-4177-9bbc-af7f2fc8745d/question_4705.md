# Q4705: from_hex: sponsor nonce checked against the wrong account

## Question
Can an unprivileged attacker reach `from_hex` (in `stacks-common/src/util/secp256k1/wasm.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that `get_sponsor_nonce`/account projection reads the origin's nonce, breaking the invariant that the nonce consumed for authority == the signer's own account nonce — leading to replay / unauthorised sequencing?

## Target
- File/function: `stacks-common/src/util/secp256k1/wasm.rs` -> `from_hex`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: `get_sponsor_nonce`/account projection reads the origin's nonce
- Invariant to test: the nonce consumed for authority == the signer's own account nonce
- Expected Immunefi impact: Critical - replay / unauthorised sequencing
- Fast validation: test a sponsored tx nonce mapping
