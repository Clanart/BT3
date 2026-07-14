# Q2549: curry tree hash overflow or underflow a boundary check via memo and proof structures

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/singleton.rs` with memo and proof structures when the same payload is parsed through public bindings make chia_rs overflow or underflow a boundary check, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/singleton.rs:26` / `curry_tree_hash`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: memo and proof structures
- Exploit idea: Drive `curry_tree_hash` through its public caller path using memo and proof structures; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
