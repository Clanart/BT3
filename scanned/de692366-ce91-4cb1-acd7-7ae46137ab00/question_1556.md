# Q1556: parse conditions allow replay across contexts via negative or oversized condition integers

## Question
Can an unprivileged attacker call the Python validation API with attacker-controlled spends targeting `parse_conditions` in `crates/chia-consensus/src/conditions.rs` with negative or oversized condition integers when the same payload is parsed through public bindings make chia_rs allow replay across contexts, violating the invariant that mempool and block validation agree on condition semantics, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:1086` / `parse_conditions`
- Entrypoint: call the Python validation API with attacker-controlled spends
- Attacker controls: negative or oversized condition integers
- Exploit idea: Drive `parse_conditions` through its public caller path using negative or oversized condition integers; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: mempool and block validation agree on condition semantics
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: build a generator with the condition variant and assert the exact ErrorCode or accepted SpendBundleConditions.
