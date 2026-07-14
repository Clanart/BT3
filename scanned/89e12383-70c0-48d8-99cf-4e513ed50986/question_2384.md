# Q2384: from clvm allow replay across contexts via curried program argument trees

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `from_clvm` in `crates/clvm-traits/src/from_clvm.rs` with curried program argument trees when a node processes data from an untrusted peer or wallet make chia_rs allow replay across contexts, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-traits/src/from_clvm.rs:17` / `from_clvm`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: curried program argument trees
- Exploit idea: Drive `from_clvm` through its public caller path using curried program argument trees; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
