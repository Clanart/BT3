# Q3992: new derive a different canonical hash via FromClvm/ToClvm enum discriminants

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `new` in `crates/clvm-utils/src/tree_hash.rs` with FromClvm/ToClvm enum discriminants when serialized bytes are validly framed but semantically adversarial make chia_rs derive a different canonical hash, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-utils/src/tree_hash.rs:13` / `new`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: FromClvm/ToClvm enum discriminants
- Exploit idea: Drive `new` through its public caller path using FromClvm/ToClvm enum discriminants; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: fuzz CLVM atoms and lists and assert typed decoding matches clvmr semantics.
