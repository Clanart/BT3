# Q1187: py new allow replay across contexts via insert/delete operation batches

## Question
Can an unprivileged attacker apply DataLayer delta operations targeting `py_new` in `crates/chia-datalayer/src/merkle/format.rs` with insert/delete operation batches when a node processes data from an untrusted peer or wallet make chia_rs allow replay across contexts, violating the invariant that absence proofs cannot be forged for present keys, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-datalayer/src/merkle/format.rs:114` / `py_new`
- Entrypoint: apply DataLayer delta operations
- Attacker controls: insert/delete operation batches
- Exploit idea: Drive `py_new` through its public caller path using insert/delete operation batches; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: absence proofs cannot be forged for present keys
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate sibling paths and assert proof rejection.
