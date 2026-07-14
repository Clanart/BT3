# Q1532: new spend allow replay across contexts via negative or oversized condition integers

## Question
Can an unprivileged attacker call the Python validation API with attacker-controlled spends targeting `new_spend` in `crates/chia-consensus/src/conditions.rs` with negative or oversized condition integers with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that mempool and block validation agree on condition semantics, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:69` / `new_spend`
- Entrypoint: call the Python validation API with attacker-controlled spends
- Attacker controls: negative or oversized condition integers
- Exploit idea: Drive `new_spend` through its public caller path using negative or oversized condition integers; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: mempool and block validation agree on condition semantics
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip through py_validate_clvm_and_signature and Rust validation and compare results.
