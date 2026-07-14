# Q2326: DiscriminantInfo accept invalid consensus data via allocator node pairs and atoms

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `DiscriminantInfo` in `crates/clvm-derive/src/helpers.rs` with allocator node pairs and atoms when the same payload is parsed through public bindings make chia_rs accept invalid consensus data, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-derive/src/helpers.rs:15` / `DiscriminantInfo`
- Entrypoint: hash curried CLVM programs
- Attacker controls: allocator node pairs and atoms
- Exploit idea: Drive `DiscriminantInfo` through its public caller path using allocator node pairs and atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test curried tree hash against executing the curried program.
