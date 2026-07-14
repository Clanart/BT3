# Q579: from skip a required validation guard via list and vector length fields

## Question
Can an unprivileged attacker convert JSON dict values into protocol structs targeting `from` in `crates/chia-protocol/src/bytes.rs` with list and vector length fields when the same payload is parsed through public bindings make chia_rs skip a required validation guard, violating the invariant that streamable parsing rejects non-canonical or trailing consensus bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:157` / `from`
- Entrypoint: convert JSON dict values into protocol structs
- Attacker controls: list and vector length fields
- Exploit idea: Drive `from` through its public caller path using list and vector length fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: streamable parsing rejects non-canonical or trailing consensus bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trailing consensus bytes never produce a valid object.
