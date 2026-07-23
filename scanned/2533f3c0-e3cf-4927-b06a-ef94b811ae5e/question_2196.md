# Q2196: Exploit reorg boundary handling in send_aggregate_txs

## Question
Can an unprivileged attacker exploit reorg timing around light-client proof blobs and their claimed heights / block hashes so `send_aggregate_txs` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the linkage between Citrea state and a replacement deposit move transaction and violating the invariant that replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action, leading to High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/sync.rs::send_aggregate_txs
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: light-client proof blobs and their claimed heights / block hashes
- Exploit idea: reorder or replay light-client proof blobs and their claimed heights / block hashes across canonical and non-canonical views
- Invariant to test: replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action
- Expected Immunefi impact: High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
