# Q1723: Confuse replacement linkage in check_evicted_commit_txs

## Question
Can an unprivileged attacker shape light-client proof blobs and their claimed heights / block hashes so `check_evicted_commit_txs` confuses replacement and non-replacement contexts, causing the L1/L2 height pair treated as finalized and safe to bridge against to inherit the wrong history and violating the invariant that Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/sync.rs::check_evicted_commit_txs
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: light-client proof blobs and their claimed heights / block hashes
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
