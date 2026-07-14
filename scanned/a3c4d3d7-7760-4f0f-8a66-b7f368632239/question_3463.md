# Q3463: update digest accept invalid consensus data via CoinState/CoinRecord transition sequences

## Question
Can an unprivileged attacker submit serialized block or spend data targeting `update_digest` in `crates/chia-protocol/src/fullblock.rs` with CoinState/CoinRecord transition sequences when values sit exactly at max/min integer boundaries make chia_rs accept invalid consensus data, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/fullblock.rs:46` / `update_digest`
- Entrypoint: submit serialized block or spend data
- Attacker controls: CoinState/CoinRecord transition sequences
- Exploit idea: Drive `update_digest` through its public caller path using CoinState/CoinRecord transition sequences; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare Rust and Python object construction from the same bytes.
