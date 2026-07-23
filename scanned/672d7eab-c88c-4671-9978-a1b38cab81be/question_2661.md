# Q2661: Accept wrong proof/network context in build_commit_transaction

## Question
Can an unprivileged attacker supply light-client proof blobs and their claimed heights / block hashes through user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync so `build_commit_transaction` accepts it without fully binding network, method-id, genesis, or height context, corrupting the storage slot/value pair used to prove deposit or withdrawal state and breaking the invariant that replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/mod.rs::build_commit_transaction
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: light-client proof blobs and their claimed heights / block hashes
- Exploit idea: omit full network, method-id, genesis, or height binding for light-client proof blobs and their claimed heights / block hashes
- Invariant to test: replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
