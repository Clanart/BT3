# Q2819: get meta group derive a different canonical hash via cross-language conversion outputs

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `get_meta_group` in `wheel/src/api.rs` with cross-language conversion outputs when a node processes data from an untrusted peer or wallet make chia_rs derive a different canonical hash, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:695` / `get_meta_group`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: cross-language conversion outputs
- Exploit idea: Drive `get_meta_group` through its public caller path using cross-language conversion outputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: call the Python API with mutable buffers and compare Rust direct output.
