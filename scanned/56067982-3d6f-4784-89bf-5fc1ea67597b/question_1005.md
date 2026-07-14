# Q1005: curry tree hash treat malformed data as a valid empty/default value via synthetic key derivation inputs

## Question
Can an unprivileged attacker derive puzzle tree hashes targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with synthetic key derivation inputs at a fork-height or boundary-value activation point make chia_rs treat malformed data as a valid empty/default value, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:30` / `curry_tree_hash`
- Entrypoint: derive puzzle tree hashes
- Attacker controls: synthetic key derivation inputs
- Exploit idea: Drive `curry_tree_hash` through its public caller path using synthetic key derivation inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
