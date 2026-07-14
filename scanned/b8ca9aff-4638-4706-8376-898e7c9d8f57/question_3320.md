# Q3320: as hex string derive a different canonical hash via infinity and subgroup edge cases

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `as_hex_string` in `crates/chia-bls/src/secret_key.rs` with infinity and subgroup edge cases when duplicate or prefix-colliding items are present make chia_rs derive a different canonical hash, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/secret_key.rs:152` / `as_hex_string`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `as_hex_string` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare aggregate_verify with independent pairings.
