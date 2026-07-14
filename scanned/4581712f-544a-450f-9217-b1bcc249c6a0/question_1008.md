# Q1008: curry tree hash commit output after an error path via memo and proof structures

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with memo and proof structures at a fork-height or boundary-value activation point make chia_rs commit output after an error path, violating the invariant that offer/CAT/NFT/DID invariants preserve asset ownership, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:65` / `curry_tree_hash`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: memo and proof structures
- Exploit idea: Drive `curry_tree_hash` through its public caller path using memo and proof structures; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: offer/CAT/NFT/DID invariants preserve asset ownership
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
