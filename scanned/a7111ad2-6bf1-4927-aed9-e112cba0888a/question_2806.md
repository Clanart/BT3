# Q2806: py calculate ip iters accept invalid consensus data via PyO3 object extraction values

## Question
Can an unprivileged attacker invoke validation helpers from Python targeting `py_calculate_ip_iters` in `wheel/src/api.rs` with PyO3 object extraction values when values sit exactly at max/min integer boundaries make chia_rs accept invalid consensus data, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:538` / `py_calculate_ip_iters`
- Entrypoint: invoke validation helpers from Python
- Attacker controls: PyO3 object extraction values
- Exploit idea: Drive `py_calculate_ip_iters` through its public caller path using PyO3 object extraction values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
