# Q3635: update digest produce a Rust/Python disagreement via streamable byte prefixes and trailing bytes

## Question
Can an unprivileged attacker relay network payload bytes through streamable decoding targeting `update_digest` in `crates/chia-protocol/src/bytes.rs` with streamable byte prefixes and trailing bytes when the same payload is parsed through public bindings make chia_rs produce a Rust/Python disagreement, violating the invariant that integer and length encodings cannot change hashes across implementations, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:257` / `update_digest`
- Entrypoint: relay network payload bytes through streamable decoding
- Attacker controls: streamable byte prefixes and trailing bytes
- Exploit idea: Drive `update_digest` through its public caller path using streamable byte prefixes and trailing bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: integer and length encodings cannot change hashes across implementations
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz length prefixes and integer encodings and assert canonical rejection.
