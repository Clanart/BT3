# Q987: new skip a required validation guard via synthetic key derivation inputs

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `new` in `crates/chia-puzzle-types/src/puzzles/cat.rs` with synthetic key derivation inputs when equivalent-looking encodings are mixed make chia_rs skip a required validation guard, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/cat.rs:50` / `new`
- Entrypoint: parse puzzle solution structures
- Attacker controls: synthetic key derivation inputs
- Exploit idea: Drive `new` through its public caller path using synthetic key derivation inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
