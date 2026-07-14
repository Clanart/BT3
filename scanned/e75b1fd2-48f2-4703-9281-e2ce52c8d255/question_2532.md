# Q2532: new skip a required validation guard via CAT/NFT/DID/offer/singleton puzzle arguments

## Question
Can an unprivileged attacker derive puzzle tree hashes targeting `new` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with CAT/NFT/DID/offer/singleton puzzle arguments at a fork-height or boundary-value activation point make chia_rs skip a required validation guard, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:97` / `new`
- Entrypoint: derive puzzle tree hashes
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `new` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz synthetic key inputs and assert signature authorization is unchanged.
