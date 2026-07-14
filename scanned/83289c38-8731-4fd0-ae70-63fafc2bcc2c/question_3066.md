# Q3066: NewCoin commit output after an error path via duplicate and contradictory ASSERT * conditions

## Question
Can an unprivileged attacker call the Python validation API with attacker-controlled spends targeting `NewCoin` in `crates/chia-consensus/src/conditions.rs` with duplicate and contradictory ASSERT_* conditions with default-enabled consensus flags make chia_rs commit output after an error path, violating the invariant that mempool and block validation agree on condition semantics, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:752` / `NewCoin`
- Entrypoint: call the Python validation API with attacker-controlled spends
- Attacker controls: duplicate and contradictory ASSERT_* conditions
- Exploit idea: Drive `NewCoin` through its public caller path using duplicate and contradictory ASSERT_* conditions; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: mempool and block validation agree on condition semantics
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip through py_validate_clvm_and_signature and Rust validation and compare results.
