# Q1235: Replay context into get_light_client_proof

## Question
Can an unprivileged attacker use user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync with attacker-controlled Citrea withdrawal/deposit logs and their ordering so `get_light_client_proof` reuses a previously accepted context, causing the light-client proof context tied to a specific Bitcoin block to be consumed twice and breaking the invariant that Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/citrea.rs::get_light_client_proof
- Entrypoint: user-triggered Citrea deposit/withdraw activity or crafted Citrea proof/log data later consumed by background sync
- Attacker controls: Citrea withdrawal/deposit logs and their ordering
- Exploit idea: reuse or replay previously consumed Citrea withdrawal/deposit logs and their ordering in a fresh context
- Invariant to test: Citrea proofs and logs must bind exactly one L1 block, one L2 state root, and one bridge state transition
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a Rust test that mutates LCP/storage-proof height, root, and slot/value bindings and assert the bridge never accepts a wrong-context Citrea state
