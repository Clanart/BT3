# Q1274: derive child sk unhardened derive a different canonical hash via Python lists of tuple spend inputs

## Question
Can an unprivileged attacker call the public Python API targeting `derive_child_sk_unhardened` in `wheel/src/api.rs` with Python lists of tuple spend inputs when the attacker can choose ordering inside a batch make chia_rs derive a different canonical hash, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:382` / `derive_child_sk_unhardened`
- Entrypoint: call the public Python API
- Attacker controls: Python lists of tuple spend inputs
- Exploit idea: Drive `derive_child_sk_unhardened` through its public caller path using Python lists of tuple spend inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
