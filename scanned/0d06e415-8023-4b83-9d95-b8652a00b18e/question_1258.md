# Q1258: Replay context into calculate_package_feerate_sat_per_kvb

## Question
Can an unprivileged attacker use user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync with attacker-controlled the timing of L1/L2 height selection around retries and finalization edges so `calculate_package_feerate_sat_per_kvb` reuses a previously accepted context, causing the L1/L2 height pair treated as finalized and safe to bridge against to be consumed twice and breaking the invariant that replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/tests.rs::calculate_package_feerate_sat_per_kvb
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: the timing of L1/L2 height selection around retries and finalization edges
- Exploit idea: reuse or replay previously consumed the timing of L1/L2 height selection around retries and finalization edges in a fresh context
- Invariant to test: replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
