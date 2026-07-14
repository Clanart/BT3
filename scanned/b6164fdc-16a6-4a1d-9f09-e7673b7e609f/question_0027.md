# Q27: SpendConditions skip a required validation guard via negative or oversized condition integers

## Question
Can an unprivileged attacker include a spend in a block generator targeting `SpendConditions` in `crates/chia-consensus/src/conditions.rs` with negative or oversized condition integers when the same payload is parsed through public bindings make chia_rs skip a required validation guard, violating the invariant that only spend conditions satisfying consensus rules are accepted, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:778` / `SpendConditions`
- Entrypoint: include a spend in a block generator
- Attacker controls: negative or oversized condition integers
- Exploit idea: Drive `SpendConditions` through its public caller path using negative or oversized condition integers; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: only spend conditions satisfying consensus rules are accepted
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test mempool flags versus block flags for the same spend.
