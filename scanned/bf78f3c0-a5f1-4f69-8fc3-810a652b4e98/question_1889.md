# Q1889: R1PublicKey overflow or underflow a boundary check via secp prehashed message/signature pairs

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `R1PublicKey` in `crates/chia-secp/src/secp256r1/public_key.rs` with secp prehashed message/signature pairs when the same payload is parsed through public bindings make chia_rs overflow or underflow a boundary check, violating the invariant that domain-separated signed messages cannot be replayed, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-secp/src/secp256r1/public_key.rs:11` / `R1PublicKey`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: secp prehashed message/signature pairs
- Exploit idea: Drive `R1PublicKey` through its public caller path using secp prehashed message/signature pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: domain-separated signed messages cannot be replayed
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz serialized keys/signatures and assert invalid encodings are rejected.
