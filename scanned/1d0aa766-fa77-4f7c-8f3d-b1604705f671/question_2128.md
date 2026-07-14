# Q2128: from collapse distinct inputs into one accepted state via trusted vs untrusted parse mode inputs

## Question
Can an unprivileged attacker parse untrusted streamable bytes targeting `from` in `crates/chia-protocol/src/bytes.rs` with trusted vs untrusted parse mode inputs when serialized bytes are validly framed but semantically adversarial make chia_rs collapse distinct inputs into one accepted state, violating the invariant that JSON conversions cannot create impossible protocol states, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:373` / `from`
- Entrypoint: parse untrusted streamable bytes
- Attacker controls: trusted vs untrusted parse mode inputs
- Exploit idea: Drive `from` through its public caller path using trusted vs untrusted parse mode inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: JSON conversions cannot create impossible protocol states
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test JSON dict conversion against streamable bytes.
