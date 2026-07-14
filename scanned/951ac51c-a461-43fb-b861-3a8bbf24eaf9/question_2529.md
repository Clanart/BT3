# Q2529: curry tree hash commit output after an error path via metadata lists and transfer programs

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with metadata lists and transfer programs with default-enabled consensus flags make chia_rs commit output after an error path, violating the invariant that offer/CAT/NFT/DID invariants preserve asset ownership, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:65` / `curry_tree_hash`
- Entrypoint: parse puzzle solution structures
- Attacker controls: metadata lists and transfer programs
- Exploit idea: Drive `curry_tree_hash` through its public caller path using metadata lists and transfer programs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: offer/CAT/NFT/DID invariants preserve asset ownership
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
