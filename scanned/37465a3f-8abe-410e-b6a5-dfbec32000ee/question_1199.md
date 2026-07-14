# Q1199: from bytes allow replay across contexts via insert/delete operation batches

## Question
Can an unprivileged attacker verify inclusion/exclusion proofs targeting `from_bytes` in `crates/chia-datalayer/src/merkle/format.rs` with insert/delete operation batches when the payload is accepted by one public API before another validates it make chia_rs allow replay across contexts, violating the invariant that absence proofs cannot be forged for present keys, and realistically causing the in-scope impact: High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state?

## Target
- File/function: `crates/chia-datalayer/src/merkle/format.rs:252` / `from_bytes`
- Entrypoint: verify inclusion/exclusion proofs
- Attacker controls: insert/delete operation batches
- Exploit idea: Drive `from_bytes` through its public caller path using insert/delete operation batches; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: absence proofs cannot be forged for present keys
- Expected Immunefi impact: High. DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state
- Fast validation: apply insert/delete batches in different orders and compare expected roots.
