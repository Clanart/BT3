# Q97: py new accept invalid consensus data via serialized block generator bytes

## Question
Can an unprivileged attacker submit a block generator targeting `py_new` in `crates/chia-consensus/src/build_compressed_block.rs` with serialized block generator bytes when the payload is accepted by one public API before another validates it make chia_rs accept invalid consensus data, violating the invariant that CLVM cost accounting is monotonic and bounded, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/build_compressed_block.rs:208` / `py_new`
- Entrypoint: submit a block generator
- Attacker controls: serialized block generator bytes
- Exploit idea: Drive `py_new` through its public caller path using serialized block generator bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM cost accounting is monotonic and bounded
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: property-test cost_left never underflows and accepted output stays within limits.
