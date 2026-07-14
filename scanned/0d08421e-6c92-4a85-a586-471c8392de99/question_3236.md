# Q3236: DerivableKey derive a different canonical hash via infinity and subgroup edge cases

## Question
Can an unprivileged attacker derive keys from attacker-controlled indexes targeting `DerivableKey` in `crates/chia-bls/src/derive_keys.rs` with infinity and subgroup edge cases when values sit exactly at max/min integer boundaries make chia_rs derive a different canonical hash, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/derive_keys.rs:3` / `DerivableKey`
- Entrypoint: derive keys from attacker-controlled indexes
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `DerivableKey` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare aggregate_verify with independent pairings.
