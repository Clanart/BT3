# Q2556: curry tree hash skip a required validation guard via CAT/NFT/DID/offer/singleton puzzle arguments

## Question
Can an unprivileged attacker derive puzzle tree hashes targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/standard.rs` with CAT/NFT/DID/offer/singleton puzzle arguments when duplicate or prefix-colliding items are present make chia_rs skip a required validation guard, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/standard.rs:19` / `curry_tree_hash`
- Entrypoint: derive puzzle tree hashes
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `curry_tree_hash` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
