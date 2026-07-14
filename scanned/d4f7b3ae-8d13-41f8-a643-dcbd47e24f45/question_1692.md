# Q1692: py generate proof skip a required validation guard via addition/removal leaf sets with duplicate coin ids

## Question
Can an unprivileged attacker compute a Merkle root from attacker-controlled leaves targeting `py_generate_proof` in `crates/chia-consensus/src/merkle_tree.rs` with addition/removal leaf sets with duplicate coin ids when serialized bytes are validly framed but semantically adversarial make chia_rs skip a required validation guard, violating the invariant that additions and removals exactly match accepted spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/merkle_tree.rs:368` / `py_generate_proof`
- Entrypoint: compute a Merkle root from attacker-controlled leaves
- Attacker controls: addition/removal leaf sets with duplicate coin ids
- Exploit idea: Drive `py_generate_proof` through its public caller path using addition/removal leaf sets with duplicate coin ids; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: additions and removals exactly match accepted spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare computed roots before and after sorted/duplicated leaf normalization.
