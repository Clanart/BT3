# Q2693: set note overflow or underflow a boundary check via iterator start indexes and blocked nodes

## Question
Can an unprivileged attacker verify inclusion/exclusion proofs targeting `set_note` in `crates/chia-datalayer/src/merkle/dot.rs` with iterator start indexes and blocked nodes when the attacker can choose ordering inside a batch make chia_rs overflow or underflow a boundary check, violating the invariant that iterators cannot skip or duplicate nodes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-datalayer/src/merkle/dot.rs:69` / `set_note`
- Entrypoint: verify inclusion/exclusion proofs
- Attacker controls: iterator start indexes and blocked nodes
- Exploit idea: Drive `set_note` through its public caller path using iterator start indexes and blocked nodes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: iterators cannot skip or duplicate nodes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: apply insert/delete batches in different orders and compare expected roots.
