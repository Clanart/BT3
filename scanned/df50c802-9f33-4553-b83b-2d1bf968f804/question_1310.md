# Q1310: run block generator derive a different canonical hash via Python lists of tuple spend inputs

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `run_block_generator` in `wheel/src/run_generator.rs` with Python lists of tuple spend inputs when equivalent-looking encodings are mixed make chia_rs derive a different canonical hash, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/run_generator.rs:28` / `run_block_generator`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: Python lists of tuple spend inputs
- Exploit idea: Drive `run_block_generator` through its public caller path using Python lists of tuple spend inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
