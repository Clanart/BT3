# Q2781: compute plot id v2 commit output after an error path via run generator API arguments

## Question
Can an unprivileged attacker invoke validation helpers from Python targeting `compute_plot_id_v2` in `wheel/src/api.rs` with run_generator API arguments when duplicate or prefix-colliding items are present make chia_rs commit output after an error path, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:144` / `compute_plot_id_v2`
- Entrypoint: invoke validation helpers from Python
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `compute_plot_id_v2` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
