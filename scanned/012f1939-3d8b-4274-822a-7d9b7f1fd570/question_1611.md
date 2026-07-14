# Q1611: BuildBlockResult reuse stale verification state via singleton fast-forward lineage proof fields

## Question
Can an unprivileged attacker build a compressed block from user-controlled spend bundles targeting `BuildBlockResult` in `crates/chia-consensus/src/build_compressed_block.rs` with singleton fast-forward lineage proof fields when a node processes data from an untrusted peer or wallet make chia_rs reuse stale verification state, violating the invariant that generator references cannot change spend meaning, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/build_compressed_block.rs:26` / `BuildBlockResult`
- Entrypoint: build a compressed block from user-controlled spend bundles
- Attacker controls: singleton fast-forward lineage proof fields
- Exploit idea: Drive `BuildBlockResult` through its public caller path using singleton fast-forward lineage proof fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generator references cannot change spend meaning
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: property-test cost_left never underflows and accepted output stays within limits.
