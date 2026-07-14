# Q3981: curry tree hash skip a required validation guard via allocator node pairs and atoms

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `curry_tree_hash` in `crates/clvm-utils/src/curry_tree_hash.rs` with allocator node pairs and atoms when duplicate or prefix-colliding items are present make chia_rs skip a required validation guard, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-utils/src/curry_tree_hash.rs:3` / `curry_tree_hash`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: allocator node pairs and atoms
- Exploit idea: Drive `curry_tree_hash` through its public caller path using allocator node pairs and atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test curried tree hash against executing the curried program.
