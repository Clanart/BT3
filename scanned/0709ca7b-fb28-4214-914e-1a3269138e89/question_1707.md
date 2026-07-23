# Q1707: Confuse replacement linkage in get_light_client_proof

## Question
Can an unprivileged attacker shape light-client proof blobs and their claimed heights / block hashes so `get_light_client_proof` confuses replacement and non-replacement contexts, causing the storage slot/value pair used to prove deposit or withdrawal state to inherit the wrong history and violating the invariant that replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/citrea.rs::get_light_client_proof
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: light-client proof blobs and their claimed heights / block hashes
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: replacement deposits must never let old move-tx linkage or stale LCP state authorize a new bridge action
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
