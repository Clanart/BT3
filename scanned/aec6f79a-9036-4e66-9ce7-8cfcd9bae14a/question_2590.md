# Q2590: new accept invalid consensus data via insert/delete operation batches

## Question
Can an unprivileged attacker verify inclusion/exclusion proofs targeting `new` in `crates/chia-datalayer/src/merkle/blob.rs` with insert/delete operation batches when values sit exactly at max/min integer boundaries make chia_rs accept invalid consensus data, violating the invariant that proofs bind key/value data to the claimed root, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-datalayer/src/merkle/blob.rs:316` / `new`
- Entrypoint: verify inclusion/exclusion proofs
- Attacker controls: insert/delete operation batches
- Exploit idea: Drive `new` through its public caller path using insert/delete operation batches; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proofs bind key/value data to the claimed root
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: iterate all nodes and assert no missing or duplicated indexes.
