# Q3170: run block generator overflow or underflow a boundary check via singleton fast-forward lineage proof fields

## Question
Can an unprivileged attacker build a compressed block from user-controlled spend bundles targeting `run_block_generator` in `crates/chia-consensus/src/run_block_generator.rs` with singleton fast-forward lineage proof fields with default-enabled consensus flags make chia_rs overflow or underflow a boundary check, violating the invariant that compressed and uncompressed generators produce identical spends, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/run_block_generator.rs:87` / `run_block_generator`
- Entrypoint: build a compressed block from user-controlled spend bundles
- Attacker controls: singleton fast-forward lineage proof fields
- Exploit idea: Drive `run_block_generator` through its public caller path using singleton fast-forward lineage proof fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: compressed and uncompressed generators produce identical spends
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: property-test cost_left never underflows and accepted output stays within limits.
