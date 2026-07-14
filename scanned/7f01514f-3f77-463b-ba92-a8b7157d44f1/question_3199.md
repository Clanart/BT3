# Q3199: ArrayTypes accept invalid consensus data via coin spend sets with matching parent and puzzle hashes

## Question
Can an unprivileged attacker validate a Merkle inclusion or exclusion proof targeting `ArrayTypes` in `crates/chia-consensus/src/merkle_tree.rs` with coin spend sets with matching parent and puzzle hashes when the same payload is parsed through public bindings make chia_rs accept invalid consensus data, violating the invariant that additions and removals exactly match accepted spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/merkle_tree.rs:25` / `ArrayTypes`
- Entrypoint: validate a Merkle inclusion or exclusion proof
- Attacker controls: coin spend sets with matching parent and puzzle hashes
- Exploit idea: Drive `ArrayTypes` through its public caller path using coin spend sets with matching parent and puzzle hashes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: additions and removals exactly match accepted spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz addition/removal sets and assert no hidden duplicate coin ids.
