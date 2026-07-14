# Q3939: RunTailCondition treat malformed data as a valid empty/default value via allocator node pairs and atoms

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `RunTailCondition` in `crates/clvm-traits/src/lib.rs` with allocator node pairs and atoms with default-enabled consensus flags make chia_rs treat malformed data as a valid empty/default value, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-traits/src/lib.rs:257` / `RunTailCondition`
- Entrypoint: hash curried CLVM programs
- Attacker controls: allocator node pairs and atoms
- Exploit idea: Drive `RunTailCondition` through its public caller path using allocator node pairs and atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
