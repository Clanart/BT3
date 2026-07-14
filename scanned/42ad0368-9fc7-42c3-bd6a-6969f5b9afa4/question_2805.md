# Q2805: py calculate sp iters commit output after an error path via run generator API arguments

## Question
Can an unprivileged attacker invoke validation helpers from Python targeting `py_calculate_sp_iters` in `wheel/src/api.rs` with run_generator API arguments when the attacker can choose ordering inside a batch make chia_rs commit output after an error path, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:524` / `py_calculate_sp_iters`
- Entrypoint: invoke validation helpers from Python
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `py_calculate_sp_iters` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
