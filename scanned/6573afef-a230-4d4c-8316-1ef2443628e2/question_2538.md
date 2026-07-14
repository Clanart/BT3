# Q2538: NftMetadata treat malformed data as a valid empty/default value via CAT/NFT/DID/offer/singleton puzzle arguments

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `NftMetadata` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with CAT/NFT/DID/offer/singleton puzzle arguments at a fork-height or boundary-value activation point make chia_rs treat malformed data as a valid empty/default value, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:178` / `NftMetadata`
- Entrypoint: parse puzzle solution structures
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `NftMetadata` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
