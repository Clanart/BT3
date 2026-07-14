# Q2122: try from accept invalid consensus data via trusted vs untrusted parse mode inputs

## Question
Can an unprivileged attacker convert JSON dict values into protocol structs targeting `try_from` in `crates/chia-protocol/src/bytes.rs` with trusted vs untrusted parse mode inputs when duplicate or prefix-colliding items are present make chia_rs accept invalid consensus data, violating the invariant that streamable parsing rejects non-canonical or trailing consensus bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:331` / `try_from`
- Entrypoint: convert JSON dict values into protocol structs
- Attacker controls: trusted vs untrusted parse mode inputs
- Exploit idea: Drive `try_from` through its public caller path using trusted vs untrusted parse mode inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: streamable parsing rejects non-canonical or trailing consensus bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip bytes through parse/stream/hash in trusted and untrusted modes.
