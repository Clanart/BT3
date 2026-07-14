# Q187: evict collapse distinct inputs into one accepted state via public key and signature byte encodings

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `evict` in `crates/chia-bls/src/bls_cache.rs` with public key and signature byte encodings when values sit exactly at max/min integer boundaries make chia_rs collapse distinct inputs into one accepted state, violating the invariant that domain-separated signed messages cannot be replayed, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/bls_cache.rs:116` / `evict`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: public key and signature byte encodings
- Exploit idea: Drive `evict` through its public caller path using public key and signature byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: domain-separated signed messages cannot be replayed
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare aggregate_verify with independent pairings.
