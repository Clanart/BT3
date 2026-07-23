# Q312: Accept wrong proof/network context in select_confirmed_tx_info

## Question
Can an unprivileged attacker supply storage-proof nodes, slots, and value encodings through user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync so `select_confirmed_tx_info` accepts it without fully binding network, method-id, genesis, or height context, corrupting the light-client proof context tied to a specific Bitcoin block and breaking the invariant that Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/sync.rs::select_confirmed_tx_info
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: storage-proof nodes, slots, and value encodings
- Exploit idea: omit full network, method-id, genesis, or height binding for storage-proof nodes, slots, and value encodings
- Invariant to test: Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
