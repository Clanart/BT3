# Q2361: decode bigint commit output after an error path via FromClvm/ToClvm enum discriminants

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `decode_bigint` in `crates/clvm-traits/src/clvm_decoder.rs` with FromClvm/ToClvm enum discriminants when the attacker can choose ordering inside a batch make chia_rs commit output after an error path, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-traits/src/clvm_decoder.rs:15` / `decode_bigint`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: FromClvm/ToClvm enum discriminants
- Exploit idea: Drive `decode_bigint` through its public caller path using FromClvm/ToClvm enum discriminants; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
