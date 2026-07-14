# Q2360: decode pair allow replay across contexts via curried program argument trees

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `decode_pair` in `crates/clvm-traits/src/clvm_decoder.rs` with curried program argument trees when the attacker can choose ordering inside a batch make chia_rs allow replay across contexts, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-traits/src/clvm_decoder.rs:13` / `decode_pair`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: curried program argument trees
- Exploit idea: Drive `decode_pair` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
