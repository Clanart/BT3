# Q2732: LeftChildFirstIterator allow replay across contexts via proof-of-inclusion paths

## Question
Can an unprivileged attacker iterate over attacker-controlled Merkle blobs targeting `LeftChildFirstIterator` in `crates/chia-datalayer/src/merkle/iterators.rs` with proof-of-inclusion paths when equivalent-looking encodings are mixed make chia_rs allow replay across contexts, violating the invariant that absence proofs cannot be forged for present keys, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-datalayer/src/merkle/iterators.rs:10` / `LeftChildFirstIterator`
- Entrypoint: iterate over attacker-controlled Merkle blobs
- Attacker controls: proof-of-inclusion paths
- Exploit idea: Drive `LeftChildFirstIterator` through its public caller path using proof-of-inclusion paths; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: absence proofs cannot be forged for present keys
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: apply insert/delete batches in different orders and compare expected roots.
