# Q1043: zstd decode path allow replay across contexts via insert/delete operation batches

## Question
Can an unprivileged attacker apply DataLayer delta operations targeting `zstd_decode_path` in `crates/chia-datalayer/src/merkle/blob.rs` with insert/delete operation batches when serialized bytes are validly framed but semantically adversarial make chia_rs allow replay across contexts, violating the invariant that absence proofs cannot be forged for present keys, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-datalayer/src/merkle/blob.rs:39` / `zstd_decode_path`
- Entrypoint: apply DataLayer delta operations
- Attacker controls: insert/delete operation batches
- Exploit idea: Drive `zstd_decode_path` through its public caller path using insert/delete operation batches; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: absence proofs cannot be forged for present keys
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz blob/delta/proof bytes and compare roots to a reference model.
