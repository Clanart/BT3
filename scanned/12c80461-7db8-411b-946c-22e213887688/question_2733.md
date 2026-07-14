# Q2733: new commit output after an error path via tree index values near block boundaries

## Question
Can an unprivileged attacker verify inclusion/exclusion proofs targeting `new` in `crates/chia-datalayer/src/merkle/iterators.rs` with tree index values near block boundaries when equivalent-looking encodings are mixed make chia_rs commit output after an error path, violating the invariant that absence proofs cannot be forged for present keys, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-datalayer/src/merkle/iterators.rs:19` / `new`
- Entrypoint: verify inclusion/exclusion proofs
- Attacker controls: tree index values near block boundaries
- Exploit idea: Drive `new` through its public caller path using tree index values near block boundaries; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: absence proofs cannot be forged for present keys
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: apply insert/delete batches in different orders and compare expected roots.
