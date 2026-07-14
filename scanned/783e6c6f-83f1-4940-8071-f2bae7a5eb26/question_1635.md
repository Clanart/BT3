# Q1635: curry single arg reuse stale verification state via singleton fast-forward lineage proof fields

## Question
Can an unprivileged attacker build a compressed block from user-controlled spend bundles targeting `curry_single_arg` in `crates/chia-consensus/src/fast_forward.rs` with singleton fast-forward lineage proof fields when equivalent-looking encodings are mixed make chia_rs reuse stale verification state, violating the invariant that generator references cannot change spend meaning, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/fast_forward.rs:18` / `curry_single_arg`
- Entrypoint: build a compressed block from user-controlled spend bundles
- Attacker controls: singleton fast-forward lineage proof fields
- Exploit idea: Drive `curry_single_arg` through its public caller path using singleton fast-forward lineage proof fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generator references cannot change spend meaning
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: property-test cost_left never underflows and accepted output stays within limits.
