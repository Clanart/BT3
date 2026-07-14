# Q3884: clone node derive a different canonical hash via FromClvm/ToClvm enum discriminants

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `clone_node` in `crates/clvm-traits/src/clvm_decoder.rs` with FromClvm/ToClvm enum discriminants when the attacker can choose ordering inside a batch make chia_rs derive a different canonical hash, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-traits/src/clvm_decoder.rs:35` / `clone_node`
- Entrypoint: hash curried CLVM programs
- Attacker controls: FromClvm/ToClvm enum discriminants
- Exploit idea: Drive `clone_node` through its public caller path using FromClvm/ToClvm enum discriminants; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
