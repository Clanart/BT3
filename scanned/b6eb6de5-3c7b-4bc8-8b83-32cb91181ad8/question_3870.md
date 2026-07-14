# Q3870: VariantInfo commit output after an error path via improper list terminators

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `VariantInfo` in `crates/clvm-derive/src/parser/variant_info.rs` with improper list terminators when duplicate or prefix-colliding items are present make chia_rs commit output after an error path, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-derive/src/parser/variant_info.rs:5` / `VariantInfo`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: improper list terminators
- Exploit idea: Drive `VariantInfo` through its public caller path using improper list terminators; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: feed improper terminators and assert only documented lists are forgiving.
