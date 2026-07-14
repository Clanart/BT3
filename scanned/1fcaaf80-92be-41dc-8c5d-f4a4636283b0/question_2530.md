# Q2530: NftStateLayerSolution accept invalid consensus data via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `NftStateLayerSolution` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with royalty and settlement puzzle fields with default-enabled consensus flags make chia_rs accept invalid consensus data, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:82` / `NftStateLayerSolution`
- Entrypoint: parse puzzle solution structures
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `NftStateLayerSolution` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
