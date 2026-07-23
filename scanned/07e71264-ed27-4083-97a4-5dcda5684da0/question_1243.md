# Q1243: Replay context into get_current_l2_block_height

## Question
Can an unprivileged attacker use user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync with attacker-controlled Citrea withdrawal/deposit logs and their ordering so `get_current_l2_block_height` reuses a previously accepted context, causing the storage slot/value pair used to prove deposit or withdrawal state to be consumed twice and breaking the invariant that replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/citrea.rs::get_current_l2_block_height
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: Citrea withdrawal/deposit logs and their ordering
- Exploit idea: reuse or replay previously consumed Citrea withdrawal/deposit logs and their ordering in a fresh context
- Invariant to test: replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
