# Q3881: decode pair allow replay across contexts via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker serialize typed values back into CLVM targeting `decode_pair` in `crates/clvm-traits/src/clvm_decoder.rs` with CLVM atoms with redundant sign bytes when serialized bytes are validly framed but semantically adversarial make chia_rs allow replay across contexts, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-traits/src/clvm_decoder.rs:13` / `decode_pair`
- Entrypoint: serialize typed values back into CLVM
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `decode_pair` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
