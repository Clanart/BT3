# Q1029: SingletonStruct treat malformed data as a valid empty/default value via synthetic key derivation inputs

## Question
Can an unprivileged attacker derive puzzle tree hashes targeting `SingletonStruct` in `crates/chia-puzzle-types/src/puzzles/singleton.rs` with synthetic key derivation inputs when duplicate or prefix-colliding items are present make chia_rs treat malformed data as a valid empty/default value, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/singleton.rs:38` / `SingletonStruct`
- Entrypoint: derive puzzle tree hashes
- Attacker controls: synthetic key derivation inputs
- Exploit idea: Drive `SingletonStruct` through its public caller path using synthetic key derivation inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
