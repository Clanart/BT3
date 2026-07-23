# Q2200: Exploit reorg boundary handling in select_confirmed_tx_info

## Question
Can an unprivileged attacker exploit reorg timing around light-client proof blobs and their claimed heights / block hashes so `select_confirmed_tx_info` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the light-client proof context tied to a specific Bitcoin block and violating the invariant that Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/sync.rs::select_confirmed_tx_info
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: light-client proof blobs and their claimed heights / block hashes
- Exploit idea: reorder or replay light-client proof blobs and their claimed heights / block hashes across canonical and non-canonical views
- Invariant to test: Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
