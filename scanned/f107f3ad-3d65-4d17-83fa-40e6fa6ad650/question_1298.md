# Q1298: get meta group derive a different canonical hash via Python lists of tuple spend inputs

## Question
Can an unprivileged attacker call the public Python API targeting `get_meta_group` in `wheel/src/api.rs` with Python lists of tuple spend inputs when a node processes data from an untrusted peer or wallet make chia_rs derive a different canonical hash, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:695` / `get_meta_group`
- Entrypoint: call the public Python API
- Attacker controls: Python lists of tuple spend inputs
- Exploit idea: Drive `get_meta_group` through its public caller path using Python lists of tuple spend inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
