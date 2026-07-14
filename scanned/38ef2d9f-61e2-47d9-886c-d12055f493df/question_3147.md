# Q3147: new treat malformed data as a valid empty/default value via CLVM program cost boundary inputs

## Question
Can an unprivileged attacker fast-forward a singleton spend with attacker-controlled lineage targeting `new` in `crates/chia-consensus/src/build_interned_block.rs` with CLVM program cost boundary inputs when the payload is accepted by one public API before another validates it make chia_rs treat malformed data as a valid empty/default value, violating the invariant that compressed and uncompressed generators produce identical spends, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/build_interned_block.rs:96` / `new`
- Entrypoint: fast-forward a singleton spend with attacker-controlled lineage
- Attacker controls: CLVM program cost boundary inputs
- Exploit idea: Drive `new` through its public caller path using CLVM program cost boundary inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: compressed and uncompressed generators produce identical spends
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: property-test cost_left never underflows and accepted output stays within limits.
