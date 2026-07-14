# Q1026: SingletonArgs reuse stale verification state via memo and proof structures

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `SingletonArgs` in `crates/chia-puzzle-types/src/puzzles/singleton.rs` with memo and proof structures when duplicate or prefix-colliding items are present make chia_rs reuse stale verification state, violating the invariant that lineage proofs cannot be reused for unrelated coins, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/singleton.rs:11` / `SingletonArgs`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: memo and proof structures
- Exploit idea: Drive `SingletonArgs` through its public caller path using memo and proof structures; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: lineage proofs cannot be reused for unrelated coins
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
