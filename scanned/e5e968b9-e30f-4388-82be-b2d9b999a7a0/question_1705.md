# Q1705: Confuse replacement linkage in collect_deposit_move_txids

## Question
Can an unprivileged attacker shape replacement-deposit linkage between Citrea state and Bitcoin move transactions so `collect_deposit_move_txids` confuses replacement and non-replacement contexts, causing the storage slot/value pair used to prove deposit or withdrawal state to inherit the wrong history and violating the invariant that replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action, leading to High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior?

## Target
- File/function: core/src/citrea.rs::collect_deposit_move_txids
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: replacement-deposit linkage between Citrea state and Bitcoin move transactions
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action
- Expected Immunefi impact: High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
