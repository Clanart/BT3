# Q1291: new collapse distinct inputs into one accepted state via Python buffer objects and memoryview slices

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `new` in `wheel/src/api.rs` with Python buffer objects and memoryview slices when a node processes data from an untrusted peer or wallet make chia_rs collapse distinct inputs into one accepted state, violating the invariant that exceptions cannot be converted into valid outputs, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:661` / `new`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: Python buffer objects and memoryview slices
- Exploit idea: Drive `new` through its public caller path using Python buffer objects and memoryview slices; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: exceptions cannot be converted into valid outputs
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
