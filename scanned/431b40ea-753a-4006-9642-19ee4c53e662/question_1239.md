# Q1239: Replay context into sign_blob_with_private_key

## Question
Can an unprivileged attacker use user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync with attacker-controlled storage-proof nodes, slots, and value encodings so `sign_blob_with_private_key` reuses a previously accepted context, causing the light-client proof context tied to a specific Bitcoin block to be consumed twice and breaking the invariant that replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/mod.rs::sign_blob_with_private_key
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: storage-proof nodes, slots, and value encodings
- Exploit idea: reuse or replay previously consumed storage-proof nodes, slots, and value encodings in a fresh context
- Invariant to test: replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
