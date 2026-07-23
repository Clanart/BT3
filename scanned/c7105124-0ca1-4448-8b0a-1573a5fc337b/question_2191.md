# Q2191: Exploit reorg boundary handling in create_reveal_script

## Question
Can an unprivileged attacker exploit reorg timing around replacement-deposit linkage between Citrea state and Bitcoin move transactions so `create_reveal_script` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the light-client proof context tied to a specific Bitcoin block and violating the invariant that Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/reveal_scripts.rs::create_reveal_script
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: replacement-deposit linkage between Citrea state and Bitcoin move transactions
- Exploit idea: reorder or replay replacement-deposit linkage between Citrea state and Bitcoin move transactions across canonical and non-canonical views
- Invariant to test: Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
