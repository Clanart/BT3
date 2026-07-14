# Q1189: NodeMetadata accept invalid consensus data via Merkle blob bytes

## Question
Can an unprivileged attacker iterate over attacker-controlled Merkle blobs targeting `NodeMetadata` in `crates/chia-datalayer/src/merkle/format.rs` with Merkle blob bytes when the payload is accepted by one public API before another validates it make chia_rs accept invalid consensus data, violating the invariant that proofs bind key/value data to the claimed root, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-datalayer/src/merkle/format.rs:162` / `NodeMetadata`
- Entrypoint: iterate over attacker-controlled Merkle blobs
- Attacker controls: Merkle blob bytes
- Exploit idea: Drive `NodeMetadata` through its public caller path using Merkle blob bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proofs bind key/value data to the claimed root
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate sibling paths and assert proof rejection.
