# Q1729: Confuse replacement linkage in calculate_feerate_sat_per_kvb

## Question
Can an unprivileged attacker shape replacement-deposit linkage between Citrea state and Bitcoin move transactions so `calculate_feerate_sat_per_kvb` confuses replacement and non-replacement contexts, causing the light-client proof context tied to a specific Bitcoin block to inherit the wrong history and violating the invariant that Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: crates/clementine-tx-sender/src/citrea/tests.rs::calculate_feerate_sat_per_kvb
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: replacement-deposit linkage between Citrea state and Bitcoin move transactions
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
