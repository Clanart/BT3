# Q1634: py finalize produce a Rust/Python disagreement via compressed spend bundle backrefs

## Question
Can an unprivileged attacker call run_block_generator/run_block_generator2 through Rust or Python bindings targeting `py_finalize` in `crates/chia-consensus/src/build_interned_block.rs` with compressed spend bundle backrefs when equivalent-looking encodings are mixed make chia_rs produce a Rust/Python disagreement, violating the invariant that generator references cannot change spend meaning, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/build_interned_block.rs:273` / `py_finalize`
- Entrypoint: call run_block_generator/run_block_generator2 through Rust or Python bindings
- Attacker controls: compressed spend bundle backrefs
- Exploit idea: Drive `py_finalize` through its public caller path using compressed spend bundle backrefs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generator references cannot change spend meaning
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: property-test cost_left never underflows and accepted output stays within limits.
