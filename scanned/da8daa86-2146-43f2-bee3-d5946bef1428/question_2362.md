# Q2362: decode curried arg accept invalid consensus data via allocator node pairs and atoms

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `decode_curried_arg` in `crates/clvm-traits/src/clvm_decoder.rs` with allocator node pairs and atoms when the attacker can choose ordering inside a batch make chia_rs accept invalid consensus data, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-traits/src/clvm_decoder.rs:20` / `decode_curried_arg`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: allocator node pairs and atoms
- Exploit idea: Drive `decode_curried_arg` through its public caller path using allocator node pairs and atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
