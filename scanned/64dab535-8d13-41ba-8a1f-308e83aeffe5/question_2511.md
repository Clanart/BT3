# Q2511: new reuse stale verification state via metadata lists and transfer programs

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `new` in `crates/chia-puzzle-types/src/puzzles/cat.rs` with metadata lists and transfer programs when equivalent-looking encodings are mixed make chia_rs reuse stale verification state, violating the invariant that lineage proofs cannot be reused for unrelated coins, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/cat.rs:71` / `new`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: metadata lists and transfer programs
- Exploit idea: Drive `new` through its public caller path using metadata lists and transfer programs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: lineage proofs cannot be reused for unrelated coins
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz synthetic key inputs and assert signature authorization is unchanged.
