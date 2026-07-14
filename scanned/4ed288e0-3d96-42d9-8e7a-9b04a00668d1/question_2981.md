# Q2981: to clvm flags overflow or underflow a boundary check via consensus constants at activation boundaries

## Question
Can an unprivileged attacker submit a boundary block/spend sequence targeting `to_clvm_flags` in `crates/chia-consensus/src/flags.rs` with consensus constants at activation boundaries at a fork-height or boundary-value activation point make chia_rs overflow or underflow a boundary check, violating the invariant that reward and fee state cannot be mis-accounted, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/flags.rs:106` / `to_clvm_flags`
- Entrypoint: submit a boundary block/spend sequence
- Attacker controls: consensus constants at activation boundaries
- Exploit idea: Drive `to_clvm_flags` through its public caller path using consensus constants at activation boundaries; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: reward and fee state cannot be mis-accounted
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: property-test height/seconds constraints against modeled CoinRecord birth data.
