# Q1691: py get root derive a different canonical hash via proofs for absent and present leaves sharing prefixes

## Question
Can an unprivileged attacker compute a Merkle root from attacker-controlled leaves targeting `py_get_root` in `crates/chia-consensus/src/merkle_tree.rs` with proofs for absent and present leaves sharing prefixes when serialized bytes are validly framed but semantically adversarial make chia_rs derive a different canonical hash, violating the invariant that additions and removals exactly match accepted spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/merkle_tree.rs:363` / `py_get_root`
- Entrypoint: compute a Merkle root from attacker-controlled leaves
- Attacker controls: proofs for absent and present leaves sharing prefixes
- Exploit idea: Drive `py_get_root` through its public caller path using proofs for absent and present leaves sharing prefixes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: additions and removals exactly match accepted spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare computed roots before and after sorted/duplicated leaf normalization.
