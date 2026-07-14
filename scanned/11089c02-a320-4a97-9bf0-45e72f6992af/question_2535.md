# Q2535: NftRoyaltyTransferPuzzleArgs reuse stale verification state via metadata lists and transfer programs

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `NftRoyaltyTransferPuzzleArgs` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with metadata lists and transfer programs at a fork-height or boundary-value activation point make chia_rs reuse stale verification state, violating the invariant that lineage proofs cannot be reused for unrelated coins, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:136` / `NftRoyaltyTransferPuzzleArgs`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: metadata lists and transfer programs
- Exploit idea: Drive `NftRoyaltyTransferPuzzleArgs` through its public caller path using metadata lists and transfer programs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: lineage proofs cannot be reused for unrelated coins
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz synthetic key inputs and assert signature authorization is unchanged.
