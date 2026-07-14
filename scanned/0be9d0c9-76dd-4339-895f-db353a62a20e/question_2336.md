# Q2336: ClvmOptions allow replay across contexts via curried program argument trees

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `ClvmOptions` in `crates/clvm-derive/src/parser/attributes.rs` with curried program argument trees when duplicate or prefix-colliding items are present make chia_rs allow replay across contexts, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-derive/src/parser/attributes.rs:46` / `ClvmOptions`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: curried program argument trees
- Exploit idea: Drive `ClvmOptions` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
