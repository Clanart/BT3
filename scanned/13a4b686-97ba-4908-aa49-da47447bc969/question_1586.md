# Q1586: from produce a Rust/Python disagreement via negative or oversized condition integers

## Question
Can an unprivileged attacker include a spend in a block generator targeting `from` in `crates/chia-consensus/src/owned_conditions.rs` with negative or oversized condition integers when the attacker can choose ordering inside a batch make chia_rs produce a Rust/Python disagreement, violating the invariant that duplicate or malformed conditions cannot relax timelocks or signatures, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/owned_conditions.rs:87` / `from`
- Entrypoint: include a spend in a block generator
- Attacker controls: negative or oversized condition integers
- Exploit idea: Drive `from` through its public caller path using negative or oversized condition integers; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: duplicate or malformed conditions cannot relax timelocks or signatures
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz condition atoms and assert validation never accepts the forbidden spend.
