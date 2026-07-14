# Q3998: deref overflow or underflow a boundary check via FromClvm/ToClvm enum discriminants

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `deref` in `crates/clvm-utils/src/tree_hash.rs` with FromClvm/ToClvm enum discriminants when the attacker can choose ordering inside a batch make chia_rs overflow or underflow a boundary check, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-utils/src/tree_hash.rs:59` / `deref`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: FromClvm/ToClvm enum discriminants
- Exploit idea: Drive `deref` through its public caller path using FromClvm/ToClvm enum discriminants; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
