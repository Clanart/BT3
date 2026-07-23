# Q1718: Confuse replacement linkage in build_reveal_transaction

## Question
Can an unprivileged attacker shape storage-proof nodes, slots, and value encodings so `build_reveal_transaction` confuses replacement and non-replacement contexts, causing the L1/L2 height pair treated as finalized and safe to bridge against to inherit the wrong history and violating the invariant that replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/mod.rs::build_reveal_transaction
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: storage-proof nodes, slots, and value encodings
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
