# Q2086: is empty accept invalid consensus data via trusted vs untrusted parse mode inputs

## Question
Can an unprivileged attacker relay network payload bytes through streamable decoding targeting `is_empty` in `crates/chia-protocol/src/bytes.rs` with trusted vs untrusted parse mode inputs with default-enabled consensus flags make chia_rs accept invalid consensus data, violating the invariant that streamable parsing rejects non-canonical or trailing consensus bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:35` / `is_empty`
- Entrypoint: relay network payload bytes through streamable decoding
- Attacker controls: trusted vs untrusted parse mode inputs
- Exploit idea: Drive `is_empty` through its public caller path using trusted vs untrusted parse mode inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: streamable parsing rejects non-canonical or trailing consensus bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test JSON dict conversion against streamable bytes.
