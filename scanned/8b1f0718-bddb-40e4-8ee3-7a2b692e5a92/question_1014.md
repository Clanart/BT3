# Q1014: NftRoyaltyTransferPuzzleArgs reuse stale verification state via memo and proof structures

## Question
Can an unprivileged attacker derive puzzle tree hashes targeting `NftRoyaltyTransferPuzzleArgs` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with memo and proof structures when the same payload is parsed through public bindings make chia_rs reuse stale verification state, violating the invariant that lineage proofs cannot be reused for unrelated coins, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:136` / `NftRoyaltyTransferPuzzleArgs`
- Entrypoint: derive puzzle tree hashes
- Attacker controls: memo and proof structures
- Exploit idea: Drive `NftRoyaltyTransferPuzzleArgs` through its public caller path using memo and proof structures; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: lineage proofs cannot be reused for unrelated coins
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
