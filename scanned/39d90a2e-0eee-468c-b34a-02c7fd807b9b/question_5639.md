# Q5639: get_tx_clarity_version: singlesig hash mode confused with a different address hash

## Question
Can an unprivileged attacker reach `get_tx_clarity_version` (in `stackslib/src/chainstate/stacks/db/transactions.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that `to_address_hash_mode`/`from_u8` maps a mode so the recovered address differs, breaking the invariant that the address the auth binds == the address the hash mode implies — leading to auth address confusion?

## Target
- File/function: `stackslib/src/chainstate/stacks/db/transactions.rs` -> `get_tx_clarity_version`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: `to_address_hash_mode`/`from_u8` maps a mode so the recovered address differs
- Invariant to test: the address the auth binds == the address the hash mode implies
- Expected Immunefi impact: Critical - auth address confusion
- Fast validation: test an edge hash-mode byte
