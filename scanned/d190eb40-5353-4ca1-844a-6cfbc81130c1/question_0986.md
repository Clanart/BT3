# Q986: EverythingWithSignatureTailArgs derive a different canonical hash via lineage proofs and launcher ids

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `EverythingWithSignatureTailArgs` in `crates/chia-puzzle-types/src/puzzles/cat.rs` with lineage proofs and launcher ids when equivalent-looking encodings are mixed make chia_rs derive a different canonical hash, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/cat.rs:45` / `EverythingWithSignatureTailArgs`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: lineage proofs and launcher ids
- Exploit idea: Drive `EverythingWithSignatureTailArgs` through its public caller path using lineage proofs and launcher ids; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
