# Q4990: secp256k1_verify: STX post-condition satisfied by burn instead of transfer

## Question
Can an unprivileged attacker reach `secp256k1_verify` (in `stacks-common/src/util/secp256k1/wasm.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that a `FungibleConditionCode` check counts a burn as the expected transfer, breaking the invariant that the movement satisfying a post-condition == the movement it names — leading to post-condition evasion?

## Target
- File/function: `stacks-common/src/util/secp256k1/wasm.rs` -> `secp256k1_verify`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: a `FungibleConditionCode` check counts a burn as the expected transfer
- Invariant to test: the movement satisfying a post-condition == the movement it names
- Expected Immunefi impact: High - post-condition evasion
- Fast validation: test a burn under a transfer post-condition
