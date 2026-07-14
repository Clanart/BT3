# Q1003: NftIntermediateLauncherArgs collapse distinct inputs into one accepted state via CAT/NFT/DID/offer/singleton puzzle argu

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `NftIntermediateLauncherArgs` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with CAT/NFT/DID/offer/singleton puzzle arguments at a fork-height or boundary-value activation point make chia_rs collapse distinct inputs into one accepted state, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:15` / `NftIntermediateLauncherArgs`
- Entrypoint: parse puzzle solution structures
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `NftIntermediateLauncherArgs` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
