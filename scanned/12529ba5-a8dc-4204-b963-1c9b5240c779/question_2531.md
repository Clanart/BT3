# Q2531: NftOwnershipLayerArgs derive a different canonical hash via memo and proof structures

## Question
Can an unprivileged attacker derive puzzle tree hashes targeting `NftOwnershipLayerArgs` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with memo and proof structures at a fork-height or boundary-value activation point make chia_rs derive a different canonical hash, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:89` / `NftOwnershipLayerArgs`
- Entrypoint: derive puzzle tree hashes
- Attacker controls: memo and proof structures
- Exploit idea: Drive `NftOwnershipLayerArgs` through its public caller path using memo and proof structures; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz synthetic key inputs and assert signature authorization is unchanged.
