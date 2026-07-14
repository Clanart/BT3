# Q1794: is all zero treat malformed data as a valid empty/default value via public key and signature byte encodings

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `is_all_zero` in `crates/chia-bls/src/secret_key.rs` with public key and signature byte encodings when serialized bytes are validly framed but semantically adversarial make chia_rs treat malformed data as a valid empty/default value, violating the invariant that domain-separated signed messages cannot be replayed, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/secret_key.rs:81` / `is_all_zero`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: public key and signature byte encodings
- Exploit idea: Drive `is_all_zero` through its public caller path using public key and signature byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: domain-separated signed messages cannot be replayed
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: test cache update/evict paths with message-public-key collisions.
