# Q3996: from reuse stale verification state via improper list terminators

## Question
Can an unprivileged attacker hash curried CLVM programs targeting `from` in `crates/clvm-utils/src/tree_hash.rs` with improper list terminators when the attacker can choose ordering inside a batch make chia_rs reuse stale verification state, violating the invariant that list terminators cannot change parsed conditions, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-utils/src/tree_hash.rs:45` / `from`
- Entrypoint: hash curried CLVM programs
- Attacker controls: improper list terminators
- Exploit idea: Drive `from` through its public caller path using improper list terminators; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: list terminators cannot change parsed conditions
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
