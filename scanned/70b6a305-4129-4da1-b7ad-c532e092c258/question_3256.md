# Q3256: update digest mis-order operations across a batch via secp prehashed message/signature pairs

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `update_digest` in `crates/chia-bls/src/gtelement.rs` with secp prehashed message/signature pairs when a node processes data from an untrusted peer or wallet make chia_rs mis-order operations across a batch, violating the invariant that cached pairing results cannot substitute different messages, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/gtelement.rs:91` / `update_digest`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: secp prehashed message/signature pairs
- Exploit idea: Drive `update_digest` through its public caller path using secp prehashed message/signature pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: cached pairing results cannot substitute different messages
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare aggregate_verify with independent pairings.
