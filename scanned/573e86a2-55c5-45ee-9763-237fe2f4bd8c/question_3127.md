# Q3127: Misbind storage-proof semantics in sign_blob_with_private_key

## Question
Can an unprivileged attacker craft light-client proof blobs and their claimed heights / block hashes so `sign_blob_with_private_key` treats one storage slot, value, or path as proving another, corrupting the light-client proof context tied to a specific Bitcoin block and violating the invariant that replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/mod.rs::sign_blob_with_private_key
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: light-client proof blobs and their claimed heights / block hashes
- Exploit idea: treat one storage slot/value/path as if it proved another using light-client proof blobs and their claimed heights / block hashes
- Invariant to test: replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
