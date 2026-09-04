# Q2794: origin_address: high-S/malleable signature accepted as a second valid form

## Question
Can an unprivileged attacker reach `origin_address` (in `stacks-codec/src/transaction.rs`) via a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions), such that the epoch verification mode admits a malleated signature recovering the same signer, breaking the invariant that accepted signatures per (signer,sighash) == the canonical one — leading to txid malleability / replay?

## Target
- File/function: `stacks-codec/src/transaction.rs` -> `origin_address`
- Entrypoint: a raw Stacks transaction the attacker crafts, signs and POSTs to a node (/v2/transactions)
- Attacker controls: the full auth structure (singlesig/multisig/order-independent/sponsored), nonce, fee, chain id, version, payload, and post-condition list, plus mutation of their own signed transactions
- Exploit idea: the epoch verification mode admits a malleated signature recovering the same signer
- Invariant to test: accepted signatures per (signer,sighash) == the canonical one
- Expected Immunefi impact: Critical - txid malleability / replay
- Fast validation: test a flipped-S signature
