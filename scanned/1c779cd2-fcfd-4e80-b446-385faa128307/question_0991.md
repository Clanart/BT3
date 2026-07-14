# Q991: curry tree hash collapse distinct inputs into one accepted state via CAT/NFT/DID/offer/singleton puzzle arguments

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/cat.rs` with CAT/NFT/DID/offer/singleton puzzle arguments with default-enabled consensus flags make chia_rs collapse distinct inputs into one accepted state, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/cat.rs:75` / `curry_tree_hash`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `curry_tree_hash` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
