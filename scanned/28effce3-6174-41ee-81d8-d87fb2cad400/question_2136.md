# Q2136: from skip a required validation guard via streamable byte prefixes and trailing bytes

## Question
Can an unprivileged attacker parse untrusted streamable bytes targeting `from` in `crates/chia-protocol/src/bytes.rs` with streamable byte prefixes and trailing bytes when the attacker can choose ordering inside a batch make chia_rs skip a required validation guard, violating the invariant that streamable parsing rejects non-canonical or trailing consensus bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:428` / `from`
- Entrypoint: parse untrusted streamable bytes
- Attacker controls: streamable byte prefixes and trailing bytes
- Exploit idea: Drive `from` through its public caller path using streamable byte prefixes and trailing bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: streamable parsing rejects non-canonical or trailing consensus bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz length prefixes and integer encodings and assert canonical rejection.
