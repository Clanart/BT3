# Q372: from bytes commit output after an error path via secp prehashed message/signature pairs

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `from_bytes` in `crates/chia-secp/src/secp256r1/public_key.rs` with secp prehashed message/signature pairs when duplicate or prefix-colliding items are present make chia_rs commit output after an error path, violating the invariant that cached pairing results cannot substitute different messages, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-secp/src/secp256r1/public_key.rs:45` / `from_bytes`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: secp prehashed message/signature pairs
- Exploit idea: Drive `from_bytes` through its public caller path using secp prehashed message/signature pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: cached pairing results cannot substitute different messages
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz serialized keys/signatures and assert invalid encodings are rejected.
