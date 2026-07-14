# Q3917: from clvm allow replay across contexts via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker decode attacker-controlled CLVM targeting `from_clvm` in `crates/clvm-traits/src/from_clvm.rs` with CLVM atoms with redundant sign bytes when the payload is accepted by one public API before another validates it make chia_rs allow replay across contexts, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-traits/src/from_clvm.rs:199` / `from_clvm`
- Entrypoint: decode attacker-controlled CLVM
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `from_clvm` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
