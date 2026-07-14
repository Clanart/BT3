# Q491: from allow replay across contexts via reward-chain and foliage fields

## Question
Can an unprivileged attacker submit serialized block or spend data targeting `from` in `crates/chia-protocol/src/program.rs` with reward-chain and foliage fields when serialized bytes are validly framed but semantically adversarial make chia_rs allow replay across contexts, violating the invariant that state transitions preserve parent-child coin relationships, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/program.rs:120` / `from`
- Entrypoint: submit serialized block or spend data
- Attacker controls: reward-chain and foliage fields
- Exploit idea: Drive `from` through its public caller path using reward-chain and foliage fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: state transitions preserve parent-child coin relationships
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare Rust and Python object construction from the same bytes.
