# Q3343: arbitrary accept invalid consensus data via duplicate public-key/message pairs

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `arbitrary` in `crates/chia-bls/src/signature.rs` with duplicate public-key/message pairs when the attacker can choose ordering inside a batch make chia_rs accept invalid consensus data, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/signature.rs:31` / `arbitrary`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: duplicate public-key/message pairs
- Exploit idea: Drive `arbitrary` through its public caller path using duplicate public-key/message pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz serialized keys/signatures and assert invalid encodings are rejected.
