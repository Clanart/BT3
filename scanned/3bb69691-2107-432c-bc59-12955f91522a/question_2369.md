# Q2369: new: singlesig hash mode confused with a different address hash

## Question
Can an unprivileged attacker reach `new` (in `stacks-codec/src/transaction.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that `to_address_hash_mode`/`from_u8` maps a mode so the recovered address differs, breaking the invariant that the address the auth binds == the address the hash mode implies — leading to auth address confusion?

## Target
- File/function: `stacks-codec/src/transaction.rs` -> `new`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: `to_address_hash_mode`/`from_u8` maps a mode so the recovered address differs
- Invariant to test: the address the auth binds == the address the hash mode implies
- Expected Immunefi impact: Critical - auth address confusion
- Fast validation: test an edge hash-mode byte
