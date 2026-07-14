# Q1028: curry tree hash overflow or underflow a boundary check via lineage proofs and launcher ids

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/singleton.rs` with lineage proofs and launcher ids when duplicate or prefix-colliding items are present make chia_rs overflow or underflow a boundary check, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/singleton.rs:26` / `curry_tree_hash`
- Entrypoint: parse puzzle solution structures
- Attacker controls: lineage proofs and launcher ids
- Exploit idea: Drive `curry_tree_hash` through its public caller path using lineage proofs and launcher ids; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
