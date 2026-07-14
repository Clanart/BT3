# Q578: from derive a different canonical hash via sized integer boundary values

## Question
Can an unprivileged attacker parse untrusted streamable bytes targeting `from` in `crates/chia-protocol/src/bytes.rs` with sized integer boundary values when the same payload is parsed through public bindings make chia_rs derive a different canonical hash, violating the invariant that streamable parsing rejects non-canonical or trailing consensus bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:151` / `from`
- Entrypoint: parse untrusted streamable bytes
- Attacker controls: sized integer boundary values
- Exploit idea: Drive `from` through its public caller path using sized integer boundary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: streamable parsing rejects non-canonical or trailing consensus bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trailing consensus bytes never produce a valid object.
