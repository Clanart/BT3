# Q853: encode atom accept invalid consensus data via CLVM atoms with redundant sign bytes

## Question
Can an unprivileged attacker serialize typed values back into CLVM targeting `encode_atom` in `crates/clvm-traits/src/clvm_encoder.rs` with CLVM atoms with redundant sign bytes when a node processes data from an untrusted peer or wallet make chia_rs accept invalid consensus data, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-traits/src/clvm_encoder.rs:51` / `encode_atom`
- Entrypoint: serialize typed values back into CLVM
- Attacker controls: CLVM atoms with redundant sign bytes
- Exploit idea: Drive `encode_atom` through its public caller path using CLVM atoms with redundant sign bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test curried tree hash against executing the curried program.
