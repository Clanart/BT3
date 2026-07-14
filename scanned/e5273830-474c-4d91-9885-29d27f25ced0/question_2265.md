# Q2265: get string commit output after an error path via plot iteration boundary values

## Question
Can an unprivileged attacker validate plot/VDF/weight proof inputs targeting `get_string` in `crates/chia-protocol/src/partial_proof.rs` with plot iteration boundary values when values sit exactly at max/min integer boundaries make chia_rs commit output after an error path, violating the invariant that weight proof data cannot imply a stronger chain than provided, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/partial_proof.rs:15` / `get_string`
- Entrypoint: validate plot/VDF/weight proof inputs
- Attacker controls: plot iteration boundary values
- Exploit idea: Drive `get_string` through its public caller path using plot iteration boundary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: weight proof data cannot imply a stronger chain than provided
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: test boundary iteration values against a simple arithmetic model.
