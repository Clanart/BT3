# Q2782: compute plot group id v2 accept invalid consensus data via PyO3 object extraction values

## Question
Can an unprivileged attacker invoke validation helpers from Python targeting `compute_plot_group_id_v2` in `wheel/src/api.rs` with PyO3 object extraction values when duplicate or prefix-colliding items are present make chia_rs accept invalid consensus data, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:163` / `compute_plot_group_id_v2`
- Entrypoint: invoke validation helpers from Python
- Attacker controls: PyO3 object extraction values
- Exploit idea: Drive `compute_plot_group_id_v2` through its public caller path using PyO3 object extraction values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
