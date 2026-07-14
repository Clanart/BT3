# Q1295: get strength allow replay across contexts via PyO3 object extraction values

## Question
Can an unprivileged attacker invoke validation helpers from Python targeting `get_strength` in `wheel/src/api.rs` with PyO3 object extraction values when a node processes data from an untrusted peer or wallet make chia_rs allow replay across contexts, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `wheel/src/api.rs:683` / `get_strength`
- Entrypoint: invoke validation helpers from Python
- Attacker controls: PyO3 object extraction values
- Exploit idea: Drive `get_strength` through its public caller path using PyO3 object extraction values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
