# Q1693: from mis-bind attacker-controlled bytes to trusted state via Merkle proof byte streams

## Question
Can an unprivileged attacker derive additions/removals for a candidate block targeting `from` in `crates/chia-consensus/src/merkle_tree.rs` with Merkle proof byte streams when serialized bytes are validly framed but semantically adversarial make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that Merkle roots uniquely bind included and excluded leaves, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/merkle_tree.rs:381` / `from`
- Entrypoint: derive additions/removals for a candidate block
- Attacker controls: Merkle proof byte streams
- Exploit idea: Drive `from` through its public caller path using Merkle proof byte streams; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Merkle roots uniquely bind included and excluded leaves
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare computed roots before and after sorted/duplicated leaf normalization.
