# Q2236: RequestSesInfo collapse distinct inputs into one accepted state via trusted vs untrusted parse mode inputs

## Question
Can an unprivileged attacker compare trusted and untrusted parse modes targeting `RequestSesInfo` in `crates/chia-protocol/src/wallet_protocol.rs` with trusted vs untrusted parse mode inputs when serialized bytes are validly framed but semantically adversarial make chia_rs collapse distinct inputs into one accepted state, violating the invariant that JSON conversions cannot create impossible protocol states, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/wallet_protocol.rs:197` / `RequestSesInfo`
- Entrypoint: compare trusted and untrusted parse modes
- Attacker controls: trusted vs untrusted parse mode inputs
- Exploit idea: Drive `RequestSesInfo` through its public caller path using trusted vs untrusted parse mode inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: JSON conversions cannot create impossible protocol states
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz length prefixes and integer encodings and assert canonical rejection.
