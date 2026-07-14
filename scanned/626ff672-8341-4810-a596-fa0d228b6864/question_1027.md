# Q1027: new collapse distinct inputs into one accepted state via CAT/NFT/DID/offer/singleton puzzle arguments

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `new` in `crates/chia-puzzle-types/src/puzzles/singleton.rs` with CAT/NFT/DID/offer/singleton puzzle arguments when duplicate or prefix-colliding items are present make chia_rs collapse distinct inputs into one accepted state, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/singleton.rs:17` / `new`
- Entrypoint: parse puzzle solution structures
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `new` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
