# Q1752: from uncompressed skip a required validation guard via public key and signature byte encodings

## Question
Can an unprivileged attacker submit aggregate signature material targeting `from_uncompressed` in `crates/chia-bls/src/public_key.rs` with public key and signature byte encodings with default-enabled consensus flags make chia_rs skip a required validation guard, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:103` / `from_uncompressed`
- Entrypoint: submit aggregate signature material
- Attacker controls: public key and signature byte encodings
- Exploit idea: Drive `from_uncompressed` through its public caller path using public key and signature byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: test cache update/evict paths with message-public-key collisions.
