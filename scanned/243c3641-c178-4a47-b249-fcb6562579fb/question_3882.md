# Q3882: decode bigint commit output after an error path via improper list terminators

## Question
Can an unprivileged attacker serialize typed values back into CLVM targeting `decode_bigint` in `crates/clvm-traits/src/clvm_decoder.rs` with improper list terminators when serialized bytes are validly framed but semantically adversarial make chia_rs commit output after an error path, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-traits/src/clvm_decoder.rs:15` / `decode_bigint`
- Entrypoint: serialize typed values back into CLVM
- Attacker controls: improper list terminators
- Exploit idea: Drive `decode_bigint` through its public caller path using improper list terminators; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
