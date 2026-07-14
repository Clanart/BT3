# Q1841: sub assign overflow or underflow a boundary check via secp prehashed message/signature pairs

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `sub_assign` in `crates/chia-bls/src/signature.rs` with secp prehashed message/signature pairs when the payload is accepted by one public API before another validates it make chia_rs overflow or underflow a boundary check, violating the invariant that domain-separated signed messages cannot be replayed, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/signature.rs:203` / `sub_assign`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: secp prehashed message/signature pairs
- Exploit idea: Drive `sub_assign` through its public caller path using secp prehashed message/signature pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: domain-separated signed messages cannot be replayed
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare aggregate_verify with independent pairings.
