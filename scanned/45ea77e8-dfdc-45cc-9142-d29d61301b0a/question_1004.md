# Q1004: new overflow or underflow a boundary check via lineage proofs and launcher ids

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `new` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with lineage proofs and launcher ids at a fork-height or boundary-value activation point make chia_rs overflow or underflow a boundary check, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:22` / `new`
- Entrypoint: parse puzzle solution structures
- Attacker controls: lineage proofs and launcher ids
- Exploit idea: Drive `new` through its public caller path using lineage proofs and launcher ids; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
