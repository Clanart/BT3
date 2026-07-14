# Q1653: run block generator2 commit output after an error path via singleton fast-forward lineage proof fields

## Question
Can an unprivileged attacker fast-forward a singleton spend with attacker-controlled lineage targeting `run_block_generator2` in `crates/chia-consensus/src/run_block_generator.rs` with singleton fast-forward lineage proof fields at a fork-height or boundary-value activation point make chia_rs commit output after an error path, violating the invariant that fast-forward output preserves singleton lineage and puzzle hash, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/run_block_generator.rs:210` / `run_block_generator2`
- Entrypoint: fast-forward a singleton spend with attacker-controlled lineage
- Attacker controls: singleton fast-forward lineage proof fields
- Exploit idea: Drive `run_block_generator2` through its public caller path using singleton fast-forward lineage proof fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: fast-forward output preserves singleton lineage and puzzle hash
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: property-test cost_left never underflows and accepted output stays within limits.
